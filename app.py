import os
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, send_from_directory
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from openai import AzureOpenAI
import time
import threading

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
    print(f"[PERFORMANCE] {category}: {execution_time:.2f}ms (Avg: {avg:.2f}ms)")

def print_performance_metrics():
    print("\n===== PERFORMANCE METRICS =====")
    for category, times in performance_metrics.items():
        if not times:
            continue
        
        avg = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)
        last = times[-1]
        
        print(f"{category}: Last={last:.2f}ms, Avg={avg:.2f}ms, Min={min_time:.2f}ms, Max={max_time:.2f}ms, Count={len(times)}")
    print("===============================\n")

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
        print(f"Error in /chat: {e}")
        return jsonify({
            "response": "I apologize, but I'm experiencing technical difficulties. Could you please try again?",
            "suggested_appointment": False
        }), 500

@app.route('/call', methods=['POST'])
def make_call():
    request_start_time = time.time() * 1000
    
    phone_number = request.json.get('phone_number')
    if not phone_number:
        return jsonify({"error": "No phone number provided"}), 400
    
    try:
        call = twilio_client.calls.create(
            to=phone_number,
            from_=TWILIO_PHONE_NUMBER,
            url=f"{request.url_root}twiml",
            machine_detection='Enable',
            async_amd=True
        )
        
        conversation_history[call.sid] = []
        
        total_time = time.time() * 1000 - request_start_time
        track_performance("total_request_time", total_time)
        
        return jsonify({"success": True, "call_sid": call.sid})
    
    except Exception as e:
        print(f"Error making call: {e}")
        return jsonify({"error": "Failed to initiate call. Please try again."}), 500

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
    response.pause(length=1)
    gather = Gather(
        input='speech dtmf',
        action='/conversation',
        method='POST',
        timeout=3,
        speech_timeout='auto',
        barge_in=True
    )
    
    gather.say(
        ".                                                                                                                   .                                                                  .Hello, this is Sam, I hope you're doing well. I am calling to check if you are looking for an automated AI agent for your business",
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
    
    #if call_sid:
    #    if call_sid + "_count" not in conversation_history:
     #       conversation_history[call_sid + "_count"] = 0
     #   conversation_history[call_sid + "_count"] += 1
    
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
        
        """if call_sid and call_sid + "_count" in conversation_history:
            conversation_history[call_sid + "_count"] += 1
        
        # Check if we've reached 5 interactions
        if conversation_history[call_sid + "_count"] >= 5:
            response.say(
                "Thank you for your time. To ensure this free demo remains within budget, "
                "this call will be disconnected in 20 seconds. I've sent a Calendly link to your phone "
                "so you can book an appointment for further discussion with Kanchan.",
                voice='Polly.Matthew-Neural'
            )
            
            # Send SMS with Calendly link
            try:
                call = twilio_client.calls(call_sid).fetch()
                phone_number = call.to
                
                sms_body = (
                    "Thank you for trying Sam, Kanchan Ghosh's appointment assistant. "
                    f"To continue your conversation, please schedule a meeting: {CALENDLY_LINK}. "
                    "For more information, visit www.ikanchan.com."
                )
                
                twilio_client.messages.create(
                    body=sms_body,
                    from_=TWILIO_PHONE_NUMBER,
                    to=phone_number
                )
            except Exception as e:
                print(f"Error sending SMS: {e}")
            
            response.pause(length=20)
            response.hangup()
            return str(response)
        """
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
                print(f"SMS sent successfully to {phone_number}")
                
                ai_response["response"] += " I've sent you an SMS with the booking link."
            except Exception as e:
                print(f"Error sending SMS: {e}")
        
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
        
        print(f"Call SID: {call_sid}")
        print(f"User: {input_text}")
        print(f"Assistant: {response_text}")
        
        total_time = time.time() * 1000 - request_start_time
        track_performance("total_request_time", total_time)
        
        return str(response)
    
    except Exception as e:
        print(f"Error in /conversation: {e}")
        response.say(
            "I'm experiencing technical difficulties. Please try again later.",
            voice='Polly.Matthew-Neural'
        )
        return str(response)

def get_ai_response(user_input, call_sid=None, web_session_id=None):
    start_time = time.time() * 1000
    
    # Get conversation history from appropriate source
    conversation_context = ""
    if call_sid and call_sid in conversation_history:
        conversation_context = "\n".join([  # Formatted history for call
            f"User: {msg['user']}\nAssistant: {msg['assistant']}"
            for msg in conversation_history[call_sid]
        ])
    elif web_session_id and web_session_id in web_chat_sessions:
        conversation_context = "\n".join([  # Formatted history for web chat
            f"User: {msg['user']}\nAssistant: {msg['assistant']}"
            for msg in web_chat_sessions[web_session_id]
        ])
    
    prompt = (
        "You are Sam, the personal assistant for Kanchan Ghosh. Kanchan is an experienced AI developer with 17 years in the field, specializing in voice bot technology. Your task is to engage users in a friendly and helpful manner and assist them in setting up meetings with Kanchan, but only after understanding their needs.\n\n"
        "### Conversation Guidelines:\n"
        "- Engage users with a friendly, polite, and professional tone.\n"
        "- Do not repeat the same phrases or introductions unnecessarily.\n"
        "- Begin with relevant small talk or context-specific responses.\n"
        "- Answer questions directly and naturally without over-explaining.\n"
        "- Only suggest scheduling a meeting once the conversation has evolved to that point, not immediately.\n"
        "- If the user asks for Kanchan’s expertise in AI or business, suggest a meeting link (Calendly).\n"
        "- If the user expresses disinterest, don’t push for a meeting.\n\n"
        "### CONVERSATION HISTORY:\n"
        f"{conversation_context}\n\n"
        "### CURRENT USER MESSAGE:\n"
        f"{user_input}\n\n"
        "Assistant: "
    )

    try:
        ai_start_time = time.time() * 1000
        
        # Call OpenAI for a response
        completion = openai_client.chat.completions.create(
            model='gpt-35-turbo',
            messages=[{"role": "system", "content": prompt},
                      {"role": "user", "content": user_input}],
            max_tokens=200,  # Increased token limit for better responses
            temperature=0.7
        )
        
        ai_time = time.time() * 1000 - ai_start_time
        track_performance("ai_response", ai_time)
        
        response_text = completion.choices[0].message.content.strip()
        
        # Check if AI suggests scheduling a meeting and clean the response
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
        elif web_session_id:
            if web_session_id not in web_chat_sessions:
                web_chat_sessions[web_session_id] = []
            web_chat_sessions[web_session_id].append({
                "user": user_input,
                "assistant": response_text,
                "timestamp": time.time() * 1000
            })
            
            # Limit web session history size
            if len(web_chat_sessions[web_session_id]) > 10:
                web_chat_sessions[web_session_id] = web_chat_sessions[web_session_id][-10:]
        
        total_time = time.time() * 1000 - start_time
        track_performance("get_ai_response", total_time)
        
        return {
            "response": response_text,
            "suggested_appointment": suggested_appointment
        }
    
    except Exception as e:
        print(f"Error in get_ai_response: {e}")
        
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
                        print(f"Removed inactive web session: {session_id}")
            # Sleep for 10 minutes before the next cleanup
            time.sleep(600)
        except Exception as e:
            print(f"Error in cleanup_sessions: {e}")
            time.sleep(600)  # If error, still sleep before retrying

def metrics_reporter():
    while True:
        try:
            print_performance_metrics()
            time.sleep(60)  # Print metrics every minute
        except Exception as e:
            print(f"Error in metrics_reporter: {e}")
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
