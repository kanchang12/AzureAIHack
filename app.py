import os
import re
import json
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, send_from_directory
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from openai import AzureOpenAI
import time
import threading
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('app.log')
    ]
)
logger = logging.getLogger('sam_appointment')

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

# Log configuration details (without sensitive info)
logger.info("Starting Sam Appointment Application")
logger.info(f"Azure OpenAI Endpoint: {AZURE_OPENAI_ENDPOINT}")
logger.info(f"Azure OpenAI API Version: {AZURE_OPENAI_API_VERSION}")
logger.info(f"Azure OpenAI Deployment Name: {AZURE_OPENAI_DEPLOYMENT_NAME}")
logger.info(f"Twilio Phone Number: {TWILIO_PHONE_NUMBER}")
logger.info(f"Calendly Link: {CALENDLY_LINK}")

# Check for missing environment variables
missing_vars = []
if not AZURE_OPENAI_API_KEY:
    missing_vars.append("AZURE_OPENAI_API_KEY")
if not AZURE_OPENAI_ENDPOINT:
    missing_vars.append("AZURE_OPENAI_ENDPOINT")
if not AZURE_OPENAI_DEPLOYMENT_NAME:
    missing_vars.append("AZURE_OPENAI_DEPLOYMENT_NAME")
if not TWILIO_ACCOUNT_SID:
    missing_vars.append("TWILIO_ACCOUNT_SID")
if not TWILIO_AUTH_TOKEN:
    missing_vars.append("TWILIO_AUTH_TOKEN")
if not TWILIO_PHONE_NUMBER:
    missing_vars.append("TWILIO_PHONE_NUMBER")

if missing_vars:
    logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
    logger.error("Application may not function correctly without these variables")

# Initialize clients
try:
    openai_client = AzureOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
        azure_endpoint=AZURE_OPENAI_ENDPOINT
    )
    logger.info("Azure OpenAI client initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Azure OpenAI client: {e}")

try:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    logger.info("Twilio client initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Twilio client: {e}")

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
    logger.debug(f"[PERFORMANCE] {category}: {execution_time:.2f}ms (Avg: {avg:.2f}ms)")

def print_performance_metrics():
    logger.info("===== PERFORMANCE METRICS =====")
    for category, times in performance_metrics.items():
        if not times:
            continue
        
        avg = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)
        last = times[-1]
        
        logger.info(f"{category}: Last={last:.2f}ms, Avg={avg:.2f}ms, Min={min_time:.2f}ms, Max={max_time:.2f}ms, Count={len(times)}")
    logger.info("===============================")

# Routes
@app.route('/')
def index():
    logger.info("Serving index page")
    return render_template('index.html')

@app.route('/template_images/<path:filename>')
def template_images(filename):
    logger.debug(f"Serving template image: {filename}")
    return send_from_directory(os.path.join(app.root_path, 'templates'), filename)

@app.route('/static/<path:path>')
def send_static(path):
    logger.debug(f"Serving static file: {path}")
    return send_from_directory('static', path)

@app.route('/chat', methods=['POST'])
def chat():
    request_start_time = time.time() * 1000
    
    user_message = request.json.get('message', '')
    session_id = request.json.get('sessionId', 'default_session')
    
    logger.info(f"Chat request received. Session ID: {session_id}")
    logger.debug(f"User message: {user_message}")
    
    # Initialize session if it doesn't exist
    if session_id not in web_chat_sessions:
        web_chat_sessions[session_id] = []
        logger.info(f"New web chat session created: {session_id}")
    
    try:
        logger.info("Getting AI response for web chat")
        ai_response = get_ai_response(user_message, None, session_id)
        
        # Handle Calendly link if appointment suggested
        response_html = ai_response["response"]
        if ai_response["suggested_appointment"]:
            logger.info("Appointment suggested, adding Calendly link")
            response_html += f'<br><br>You can <a href="{CALENDLY_LINK}" target="_blank">schedule a meeting here</a>.'
        
        result = {
            "response": response_html,
            "suggested_appointment": ai_response["suggested_appointment"],
            "sessionId": session_id
        }
        
        total_time = time.time() * 1000 - request_start_time
        track_performance("total_request_time", total_time)
        logger.info(f"Chat request processed in {total_time:.2f}ms")
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"Error in /chat: {e}", exc_info=True)
        return jsonify({
            "response": "I apologize, but I'm experiencing technical difficulties. Could you please try again?",
            "suggested_appointment": False
        }), 500

@app.route('/call', methods=['POST'])
def make_call():
    request_start_time = time.time() * 1000
    
    phone_number = request.json.get('phone_number')
    logger.info(f"Call request received for phone number: {phone_number[:6]}****")
    
    if not phone_number:
        logger.error("No phone number provided for call")
        return jsonify({"error": "No phone number provided"}), 400
    
    try:
        # Construct the full URL for the TwiML endpoint
        host = request.host_url.rstrip('/')
        twiml_url = f"{host}/twiml"
        logger.info(f"TwiML URL for call: {twiml_url}")
        
        call = twilio_client.calls.create(
            to=phone_number,
            from_=TWILIO_PHONE_NUMBER,
            url=twiml_url,
            machine_detection='Enable',
            async_amd=True
        )
        
        logger.info(f"Call initiated successfully. SID: {call.sid}")
        conversation_history[call.sid] = []
        
        total_time = time.time() * 1000 - request_start_time
        track_performance("total_request_time", total_time)
        
        return jsonify({"success": True, "call_sid": call.sid})
    
    except Exception as e:
        logger.error(f"Error making call: {e}", exc_info=True)
        return jsonify({"error": "Failed to initiate call. Please try again."}), 500

@app.route('/twiml', methods=['GET', 'POST'])
def twiml_response():
    call_sid = request.form.get('CallSid')
    machine_result = request.form.get('AnsweredBy')
    
    logger.info(f"TwiML request received. Call SID: {call_sid}, Answered by: {machine_result}")
    logger.debug(f"TwiML request form data: {request.form}")
    
    response = VoiceResponse()
    
    # If answering machine is detected, leave a voicemail
    if machine_result == 'machine_start':
        logger.info("Answering machine detected, leaving voicemail")
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
    
    # Added pauses at the start to allow the connection to stabilize
    gather.say(
        ".                                                                                                                   .                                                                  .Hello, this is Sam, I hope you're doing well. I am calling to check if you are looking for an automated AI agent for your business",
        voice='Polly.Matthew-Neural'
    )
    
    response.append(gather)
    
 
    
    logger.info("TwiML response generated successfully")
    logger.debug(f"TwiML response: {str(response)}")
    
    return str(response)



@app.route('/conversation', methods=['POST'])
def handle_conversation():
    request_start_time = time.time() * 1000
    
    user_speech = request.form.get('SpeechResult', '')
    call_sid = request.form.get('CallSid')
    digits = request.form.get('Digits', '')
    
    logger.info(f"Conversation request received. Call SID: {call_sid}")
    logger.debug(f"User speech: {user_speech}")
    logger.debug(f"Digits pressed: {digits}")
    
    response = VoiceResponse()
    
    # Handle hang up
    if digits == '9' or any(word in user_speech.lower() for word in ['goodbye', 'bye', 'hang up', 'end call']):
        logger.info("User requested to end the call")
        response.say(
            "Thank you for your time. If you'd like to schedule an appointment later, you can visit our website. Have a great day!",
            voice='Polly.Matthew-Neural'
        )
        response.hangup()
        return str(response)
    
    try:
        input_text = user_speech or (f"Button {digits} pressed" if digits else "Hello")
        logger.info(f"Processing conversation input: {input_text}")
        
        logger.info("Getting AI response for phone conversation")
        ai_response = get_ai_response(input_text, call_sid)
        
        # SMS handling for appointments
        if ai_response["suggested_appointment"] and call_sid:
            try:
                call = twilio_client.calls(call_sid).fetch()
                phone_number = call.to
                logger.info(f"Appointment suggested. Sending SMS to {phone_number[:6]}****")
                
                sms_body = (
                    "Hello! This is Sam, Kanchan Ghosh's appointment assistant. "
                    "Kanchan is an AI developer with 17 years of experience, specializing in voice bot technology. "
                    f"You can schedule a meeting with him here: {CALENDLY_LINK}. "
                    "For more about Kanchan's work, visit www.ikanchan.com."
                )
                
                message = twilio_client.messages.create(
                    body=sms_body,
                    from_=TWILIO_PHONE_NUMBER,
                    to=phone_number
                )
                logger.info(f"SMS sent successfully. SID: {message.sid}")
                
                ai_response["response"] += " I've sent you an SMS with the booking link."
            except Exception as e:
                logger.error(f"Error sending SMS: {e}", exc_info=True)
        
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
        response_text = re.sub(r'<[^>]*>', '', response_text)
        
        response.pause(length=1) #Added 1 second pause.
        gather.say(response_text, voice='Polly.Matthew-Neural')
        
        response.pause(length=1)
        response.append(gather)
        
        logger.info(f"Call SID: {call_sid}")
        logger.info(f"User: {input_text}")
        logger.info(f"Assistant: {response_text}")
        
        total_time = time.time() * 1000 - request_start_time
        track_performance("total_request_time", total_time)
        
        return str(response)
    
    except Exception as e:
        logger.error(f"Error in /conversation: {e}", exc_info=True)
        response.say(
            "I'm experiencing technical difficulties. Please try again later.",
            voice='Polly.Matthew-Neural'
        )
        return str(response)

def get_ai_response(user_input, call_sid=None, web_session_id=None):
    start_time = time.time() * 1000
    
    logger.debug(f"Getting AI response for: call_sid={call_sid}, web_session_id={web_session_id}")
    logger.debug(f"User input: {user_input}")
    
    # Get conversation history from appropriate source
    conversation_context = ""
    if call_sid and call_sid in conversation_history:
        logger.debug(f"Using call conversation history for {call_sid}")
        conversation_context = "\n".join([
            f"User: {msg['user']}\nAssistant: {msg['assistant']}"
            for msg in conversation_history[call_sid]
        ])
    elif web_session_id and web_session_id in web_chat_sessions:
        logger.debug(f"Using web chat history for session {web_session_id}")
        conversation_context = "\n".join([
            f"User: {msg['user']}\nAssistant: {msg['assistant']}"
            for msg in web_chat_sessions[web_session_id]
        ])
    
    prompt = (
        "You are Sam, the personal appointment setter for Kanchan Ghosh. He is a male (He/him/his) Kanchan is an AI developer and freelancer with 17 years of diverse industry experience, specializing in voice bot development. Your role is to professionally and politely assist users in setting up meetings with Kanchan.\n\n"
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

    try:
        ai_start_time = time.time() * 1000
        logger.info("Sending request to Azure OpenAI")
        
        completion = openai_client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT_NAME,  # Use the deployment name instead of hardcoded 'gpt-35-turbo'
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_input}
            ],
            max_tokens=150,
            temperature=0.7
        )
        
        ai_time = time.time() * 1000 - ai_start_time
        track_performance("ai_response", ai_time)
        logger.info(f"Received response from Azure OpenAI in {ai_time:.2f}ms")
        
        response_text = completion.choices[0].message.content.strip()
        suggested_appointment = "[Appointment Suggested]" in response_text
        response_text = response_text.replace("[Appointment Suggested]", "")
        
        logger.debug(f"AI response: {response_text}")
        logger.debug(f"Suggested appointment: {suggested_appointment}")
        
        # Save to appropriate conversation history
        if call_sid:
            if call_sid not in conversation_history:
                conversation_history[call_sid] = []
                logger.debug(f"Created new conversation history for call {call_sid}")
                
            conversation_history[call_sid].append({
                "user": user_input,
                "assistant": response_text,
                "timestamp": time.time() * 1000
            })
            
            # Limit conversation history size
            if len(conversation_history[call_sid]) > 10:
                conversation_history[call_sid] = conversation_history[call_sid][-10:]
                logger.debug(f"Trimmed conversation history for call {call_sid}")
        elif web_session_id:
            web_chat_sessions[web_session_id].append({
                "user": user_input,
                "assistant": response_text,
                "timestamp": time.time() * 1000
            })
            
            # Limit web session history size
            if len(web_chat_sessions[web_session_id]) > 10:
                web_chat_sessions[web_session_id] = web_chat_sessions[web_session_id][-10:]
                logger.debug(f"Trimmed conversation history for web session {web_session_id}")
        
        total_time = time.time() * 1000 - start_time
        track_performance("get_ai_response", total_time)
        
        return {
            "response": response_text,
            "suggested_appointment": suggested_appointment
        }
    
    except Exception as e:
        logger.error(f"Error in get_ai_response: {e}", exc_info=True)
        
        error_time = time.time() * 1000 - start_time
        track_performance("get_ai_response", error_time)
        
        return {
            "response": "I apologize, but I'm having trouble processing your request. Could you please try again?",
            "suggested_appointment": False
        }

# Session cleanup - remove inactive web sessions after 30 minutes
def cleanup_sessions():
    logger.info("Session cleanup thread started")
    while True:
        try:
            now = time.time() * 1000
            removed_count = 0
            for session_id, history in list(web_chat_sessions.items()):
                if history:
                    last_message_time = history[-1].get("timestamp", 0)
                    if now - last_message_time > 30 * 60 * 1000:  # 30 minutes
                        del web_chat_sessions[session_id]
                        removed_count += 1
            
            if removed_count > 0:
                logger.info(f"Removed {removed_count} inactive web sessions")
                
            # Sleep for 10 minutes before the next cleanup
            time.sleep(600)
        except Exception as e:
            logger.error(f"Error in cleanup_sessions: {e}", exc_info=True)
            time.sleep(600)  # If error, still sleep before retrying

def metrics_reporter():
    logger.info("Metrics reporter thread started")
    while True:
        try:
            print_performance_metrics()
            time.sleep(60)  # Print metrics every minute
        except Exception as e:
            logger.error(f"Error in metrics_reporter: {e}", exc_info=True)
            time.sleep(60)  # If error, still sleep before retrying

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    logger.info("Health check requested")
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "web_sessions": len(web_chat_sessions),
        "call_conversations": len([k for k in conversation_history.keys() if not k.endswith("_count")])
    })

if __name__ == '__main__':
    # Using environment variable PORT or default to 8000
    port = int(os.environ.get('PORT', 8000))
    
    # Log the port we're using
    logger.info(f"Starting Flask app on port {port}")
    
    # Start session cleanup in a separate thread
    cleanup_thread = threading.Thread(target=cleanup_sessions, daemon=True)
    cleanup_thread.start()
    logger.info("Session cleanup thread started")

    # Start performance metrics printing in a separate thread
    metrics_thread = threading.Thread(target=metrics_reporter, daemon=True)
    metrics_thread.start()
    logger.info("Metrics reporter thread started")

    # Add a message about starting the server
    logger.info(f"Server is starting at http://0.0.0.0:{port}")
    
    # Run the Flask app
    try:
        app.run(host='0.0.0.0', port=port, debug=False)
    except Exception as e:
        logger.critical(f"Failed to start the Flask application: {e}", exc_info=True)
