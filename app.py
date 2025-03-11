import os
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, send_from_directory
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from openai import AzureOpenAI
import time
import threading
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s')

app = Flask(__name__, static_url_path='')

# Configuration
AZURE_OPENAI_API_KEY = os.environ.get('AZURE_OPENAI_API_KEY')
AZURE_OPENAI_ENDPOINT = os.environ.get('AZURE_OPENAI_ENDPOINT')
AZURE_OPENAI_API_VERSION = os.environ.get('AZURE_OPENAI_API_VERSION', '2023-05-15')
AZURE_OPENAI_DEPLOYMENT_NAME = os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME')
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')
CALENDLY_LINK = "https://calendly.com/kanchan-g12/let-s-connect-30-minute-exploratory-call"

# Initialize clients
openai_client = AzureOpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    api_version=AZURE_OPENAI_API_VERSION,
    azure_endpoint=AZURE_OPENAI_ENDPOINT
)
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Store conversation histories
conversation_history = {}
web_chat_sessions = {}

# Performance tracking
performance_metrics = {
    "ai_response": [],
    "total_request_time": []
}

def track_performance(category, execution_time):
    if category not in performance_metrics:
        performance_metrics[category] = []
    
    performance_metrics[category].append(execution_time)
    
    # Keep only the last 100 measurements
    if len(performance_metrics[category]) > 100:
        performance_metrics[category].pop(0)
    
    # Calculate average time
    avg = sum(performance_metrics[category]) / len(performance_metrics[category])
    logging.info(f"[PERFORMANCE] {category}: {execution_time:.2f}ms (Avg: {avg:.2f}ms)")

def print_performance_metrics():
    logging.info("\n===== PERFORMANCE METRICS =====")
    for category, times in performance_metrics.items():
        if not times:
            continue
        
        avg = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)
        last = times[-1]
        
        logging.info(f"{category}: Last={last:.2f}ms, Avg={avg:.2f}ms, Min={min_time:.2f}ms, Max={max_time:.2f}ms, Count={len(times)}")
    logging.info("===============================\n")

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/template_images/<path:filename>')
def template_images(filename):
    return send_from_directory(os.path.join(app.root_path, 'templates'), filename)

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

@app.route('/chat', methods=['POST'])
def chat():
    request_start_time = time.time() * 1000
    
    user_message = request.json.get('message', '')
    session_id = request.json.get('sessionId', 'default_session')
    
    # Initialize session if it doesn't exist
    if session_id not in web_chat_sessions:
        web_chat_sessions[session_id] = []
    
    try:
        ai_response = get_ai_response(user_message, None, session_id)
        
        # Handle Calendly link if appointment suggested
        response_html = ai_response["response"]
        if ai_response["suggested_appointment"]:
            response_html += f'<br><br>You can <a href="{CALENDLY_LINK}" target="_blank">schedule a meeting here</a>.'
        
        result = {
            "response": response_html,
            "suggested_appointment": ai_response["suggested_appointment"],
            "sessionId": session_id
        }
        
        total_time = time.time() * 1000 - request_start_time
        track_performance("total_request_time", total_time)
        
        return jsonify(result)
    
    except Exception as e:
        logging.error(f"Error in /chat: {e}")
        return jsonify({
            "response": "I apologize, but I'm experiencing technical difficulties. Could you please try again?",
            "suggested_appointment": False
        }), 500

@app.route('/call', methods=['POST'])
def make_call():
    import traceback
    
    request_start_time = time.time() * 1000
    
    phone_number = request.json.get('phone_number')
    if not phone_number:
        logging.error("Call failed: No phone number provided")
        return jsonify({"error": "No phone number provided"}), 400
    
    try:
        # Validate inputs and environment variables
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
            logging.error("Call failed: Missing Twilio credentials")
            return jsonify({"error": "Twilio configuration incomplete"}), 500
        
        # Log the attempt with masked credentials for security
        logging.info(f"Attempting to call: {phone_number}")
        logging.info(f"Using Twilio number: {TWILIO_PHONE_NUMBER}")
        logging.info(f"URL for TwiML: {request.url_root}twiml")
        
        # Create the call with additional logging
        try:
            call = twilio_client.calls.create(
                to=phone_number,
                from_=TWILIO_PHONE_NUMBER,
                url=f"{request.url_root}twiml",
                machine_detection='Enable',
                async_amd=True
            )
            logging.info(f"Call successfully initiated with SID: {call.sid}")
        except Exception as twilio_err:
            logging.error(f"Twilio API error: {str(twilio_err)}")
            logging.error(traceback.format_exc())
            return jsonify({"error": f"Twilio API error: {str(twilio_err)}"}), 500
        
        # Initialize conversation history for this call
        conversation_history[call.sid] = []
        
        # Track performance metrics
        total_time = time.time() * 1000 - request_start_time
        track_performance("total_request_time", total_time)
        
        return jsonify({"success": True, "call_sid": call.sid})
    
    except Exception as e:
        # Detailed logging of the exception
        logging.error(f"Unexpected error in make_call: {str(e)}")
        logging.error(traceback.format_exc())
        
        # Check for specific error types
        error_message = str(e).lower()
        if "authentication" in error_message or "auth" in error_message:
            return jsonify({"error": "Authentication failed with Twilio. Please check credentials."}), 401
        elif "rate" in error_message and "limit" in error_message:
            return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429
        elif "not found" in error_message or "404" in error_message:
            return jsonify({"error": "Resource not found. Please check configuration."}), 404
        else:
            return jsonify({"error": f"Failed to initiate call: {str(e)}"}), 500

@app.route('/twiml', methods=['POST'])
def twiml_response():
    response = VoiceResponse()
    call_sid = request.form.get('CallSid')
    machine_result = request.form.get('AnsweredBy')

    # If answering machine is detected, leave a voicemail
    if machine_result == 'machine_start':
        response.say(
            "Hello, this is Sam, I hope you're doing well. I am calling to check if you are looking for an automated AI agent for your business",
            voice='Polly.Matthew-Neural')
        response.hangup()
        return str(response)
    
    gather = Gather(
        input='speech dtmf',
        action='/conversation',
        method='POST',
        timeout=3,
        speech_timeout='auto',
        barge_in=True
    )
    
    gather.say(
        "Hello, this is Sam, Kanchan Ghosh's appointment assistant. How can I assist you today?",
        voice='Polly.Matthew-Neural'
    )
    
    response.append(gather)
    response.redirect('/conversation')
    
    return str(response)

@app.route('/conversation', methods=['POST'])
def handle_conversation():
    request_start_time = time.time() * 1000
    
    user_speech = request.form.get('SpeechResult', '')
    call_sid = request.form.get('CallSid')
    digits = request.form.get('Digits', '')
    
    response = VoiceResponse()
    
    # Handle hang up
    if digits == '9' or any(word in user_speech.lower() for word in ['goodbye', 'bye', 'hang up', 'end call']):
        response.say(
            "Thank you for your time. If you'd like to schedule an appointment later, you can visit our website. Have a great day!",
            voice='Polly.Matthew-Neural'
        )
        response.hangup()
        return str(response)
    
    try:
        input_text = user_speech or (f"Button {digits} pressed" if digits else "Hello")
        
        ai_response = get_ai_response(input_text, call_sid)
        
        # SMS handling for appointments
        if ai_response["suggested_appointment"] and call_sid:
            try:
                call = twilio_client.calls(call_sid).fetch()
                phone_number = call.to
                
                sms_body = (
                    "Hello! This is Sam, Kanchan Ghosh's appointment assistant. "
                    "Kanchan is an AI developer with 17 years of experience, specializing in voice bot technology. "
                    f"You can schedule a meeting with him here: {CALENDLY_LINK}. "
                    "For more about Kanchan's work, visit www.ikanchan.com."
                )
                
                twilio_client.messages.create(
                    body=sms_body,
                    from_=TWILIO_PHONE_NUMBER,
                    to=phone_number
                )
                logging.info(f"SMS sent successfully to {phone_number}")
                
                ai_response["response"] += " I've sent you an SMS with the booking link."
            except Exception as e:
                logging.error(f"Error sending SMS: {e}")
        
        gather = Gather(
            input='speech dtmf',
            action='/conversation',
            method='POST',
            timeout=5,
            speech_timeout='auto',
            barge_in=True
        )
        
        # Clean response text (remove HTML tags)
        response_text = ai_response["response"].replace("<br>", " ")
        # Better HTML tag removal
        import re
        response_text = re.sub(r'<[^>]*>', '', response_text)
        
        gather.say(response_text, voice='Polly.Matthew-Neural')
        
        response.pause(length=1)
        response.append(gather)
        
        logging.info(f"Call SID: {call_sid}")
        logging.info(f"User: {input_text}")
        logging.info(f"Assistant: {response_text}")
        
        total_time = time.time() * 1000 - request_start_time
        track_performance("total_request_time", total_time)
        
        return str(response)
    
    except Exception as e:
        logging.error(f"Error in /conversation: {e}")
        response.say(
            "I'm experiencing technical difficulties. Please try again later.",
            voice='Polly.Matthew-Neural'
        )
        return str(response)

def get_ai_response(user_input, call_sid=None, web_session_id=None):
    import traceback
    
    start_time = time.time() * 1000
    
    try:
        # Validate that OpenAI credentials are present
        if not AZURE_OPENAI_API_KEY or not AZURE_OPENAI_ENDPOINT:
            logging.error("Missing OpenAI API credentials")
            return {
                "response": "I apologize, but our AI service is not configured correctly. Please contact support.",
                "suggested_appointment": False
            }
        
        # Get conversation history from appropriate source
        conversation_context = ""
        if call_sid and call_sid in conversation_history:
            conversation_context = "\n".join([
                f"User: {msg['user']}\nAssistant: {msg['assistant']}"
                for msg in conversation_history[call_sid]
            ])
            logging.debug(f"Using call history for SID: {call_sid}, messages: {len(conversation_history[call_sid])}")
        elif web_session_id and web_session_id in web_chat_sessions:
            conversation_context = "\n".join([
                f"User: {msg['user']}\nAssistant: {msg['assistant']}"
                for msg in web_chat_sessions[web_session_id]
            ])
            logging.debug(f"Using web session history for ID: {web_session_id}, messages: {len(web_chat_sessions[web_session_id])}")
        else:
            logging.debug("No existing conversation history found - starting fresh")
        
        # Construct the prompt
        prompt = (
            "You are Sam, the personal appointment setter for Kanchan Ghosh. He is a male (He/him/his) Kanchan is an AI developer and freelancer with 17 years of diverse industry experience, specializing in voice bot technology. "
            "## Conversation Guidelines:\n"
            "- Start with a warm and friendly greeting.\n"
            "- Introduce Kanchan briefly: 'Kanchan is an experienced AI developer specializing in voice bot technology.'\n"
            "- Engage users in light conversation before smoothly transitioning into discussing business needs.\n"
            "- If the user expresses interest in AI solutions or business collaboration, suggest scheduling a meeting.\n"
            "- When offering a meeting, provide this Calendly link: [Calendly Link]\n"
            "- If needed, guide users to more information on Kanchan's website: www.ikanchan.com.\n"
            "- Keep responses **clear, concise, and focused**.\n\n"
            "### CONVERSATION HISTORY:\n"
            f"{conversation_context}\n\n"
            "### CURRENT USER MESSAGE:\n"
            f"{user_input}\n\n"
            "Remember: Be friendly, professional, and guide users to set up a meeting when appropriate.\n"
        )

        # Make the OpenAI API call with proper error handling
        ai_start_time = time.time() * 1000
        
        try:
            logging.debug(f"Sending request to OpenAI. User input: '{user_input}'")
            completion = openai_client.chat.completions.create(
                model='gpt-35-turbo',
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_input}
                ],
                max_tokens=150,
                temperature=0.7
            )
            
            ai_time = time.time() * 1000 - ai_start_time
            track_performance("ai_response", ai_time)
            
            response_text = completion.choices[0].message.content.strip()
            logging.debug(f"Received response from OpenAI: '{response_text}'")
            
        except Exception as openai_err:
            logging.error(f"OpenAI API error: {str(openai_err)}")
            logging.error(traceback.format_exc())
            
            # Check for common OpenAI errors
            error_message = str(openai_err).lower()
            if "rate" in error_message and "limit" in error_message:
                response_text = "I'm very sorry, but our AI service is experiencing high demand. Could we try again in a moment?"
            elif "token" in error_message or "auth" in error_message:
                response_text = "I apologize, but I'm having trouble connecting to our AI service. Please try again later."
            else:
                response_text = "I apologize, but I'm having trouble processing your request. Could you please try again?"
            
            return {
                "response": response_text,
                "suggested_appointment": False
            }
        
        # Process the response
        suggested_appointment = "[Appointment Suggested]" in response_text
        response_text = response_text.replace("[Appointment Suggested]", "")
        
        # Save to appropriate conversation history
        if call_sid:
            if call_sid not in conversation_history:
                conversation_history[call_sid] = []
                
            conversation_history[call_sid].append({
                "user": user_input,
                "assistant": response_text,
                "timestamp": time.time() * 1000
            })
            
            # Limit conversation history size
            if len(conversation_history[call_sid]) > 10:
                conversation_history[call_sid] = conversation_history[call_sid][-10:]
                
            logging.debug(f"Updated call history for SID: {call_sid}, now has {len(conversation_history[call_sid])} messages")
                
        elif web_session_id:
            web_chat_sessions[web_session_id].append({
                "user": user_input,
                "assistant": response_text,
                "timestamp": time.time() * 1000
            })
            
            # Limit web session history size
            if len(web_chat_sessions[web_session_id]) > 10:
                web_chat_sessions[web_session_id] = web_chat_sessions[web_session_id][-10:]
                
            logging.debug(f"Updated web session for ID: {web_session_id}, now has {len(web_chat_sessions[web_session_id])} messages")
        
        total_time = time.time() * 1000 - start_time
        track_performance("get_ai_response", total_time)
        
        return {
            "response": response_text,
            "suggested_appointment": suggested_appointment
        }
    
    except Exception as e:
        logging.error(f"Unexpected error in get_ai_response: {str(e)}")
        logging.error(traceback.format_exc())
        
        error_time = time.time() * 1000 - start_time
        track_performance("get_ai_response", error_time)
        
        return {
            "response": "I apologize, but I'm having trouble processing your request. Could you please try again?",
            "suggested_appointment": False
        }
# Session cleanup - remove inactive web sessions after 30 minutes
def cleanup_sessions():
    while True:
        try:
            now = time.time() * 1000
            for session_id, history in list(web_chat_sessions.items()):
                if history:
                    last_message_time = history[-1].get("timestamp", 0)
                    if now - last_message_time > 30 * 60 * 1000:  # 30 minutes
                        del web_chat_sessions[session_id]
                        logging.info(f"Removed inactive web session: {session_id}")
            # Sleep for 10 minutes before the next cleanup
            time.sleep(600)
        except Exception as e:
            logging.error(f"Error in cleanup_sessions: {e}")
            time.sleep(600)  # If error, still sleep before retrying

def metrics_reporter():
    while True:
        try:
            print_performance_metrics()
            time.sleep(60)  # Print metrics every minute
        except Exception as e:
            logging.error(f"Error in metrics_reporter: {e}")
            time.sleep(60)  # If error, still sleep before retrying

if __name__ == '__main__':
    # Start session cleanup in a separate thread
    cleanup_thread = threading.Thread(target=cleanup_sessions)
    cleanup_thread.daemon = True
    cleanup_thread.start()
    
    # Start performance metrics printing in a separate thread
    metrics_thread = threading.Thread(target=metrics_reporter)
    metrics_thread.daemon = True
    metrics_thread.start()
    
    # Run the Flask app
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
