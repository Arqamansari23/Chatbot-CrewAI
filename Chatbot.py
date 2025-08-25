from flask import Flask, request, jsonify, render_template, session, send_from_directory
from flask_cors import CORS
import os
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
import uuid
import time
import threading
import sqlite3
import json
import re
load_dotenv()
# CrewAI imports
from crewai import Agent, Task, Crew, Process
from crewai.tools import tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.prompts import PromptTemplate
# Initialize Flask app

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')
CORS(app)  # Enable CORS for all routes
# Configure static folder for logo
app.config['UPLOAD_FOLDER'] = 'static'

# Session management
session_crews = {}
session_last_activity = {}
session_conversations = {}  # Store conversation history for each session
session_lead_data = {}  # Store lead qualification data for each session
session_consultation_data = {}  # Store consultation data for each session
SESSION_TIMEOUT = 1800  # 30 minutes in seconds

MAX_CONVERSATION_LENGTH = 10  # Maximum number of messages to keep in context










# ============ DATABASE SETUP ============
DATABASE_PATH = 'leads.db'

def get_db_connection():
    """Create a database connection to the SQLite database"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database():
    """Initialize the database with the required tables"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create leads table with updated schema
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            company_name TEXT,
            project_description TEXT NOT NULL,
            timeline TEXT NOT NULL,
            project_type TEXT NOT NULL,
            status TEXT NOT NULL,
            full_conversation TEXT
        )
        ''')
        
        # Create consultant table for consultation requests
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS consultant (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            consultation_type TEXT DEFAULT 'General Consultation',
            status TEXT DEFAULT 'New Request',
            full_conversation TEXT
        )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ Database initialized successfully with both leads and consultant tables")
        return True
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        return False

# Initialize database on startup
initialize_database()













# ============ LLM SETUP ============
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.1,
    max_tokens=500
)







# ============ CUSTOM RAG SETUP ============
COMPANY_NAME = "Genetech Solutions"
vectorstore = None
rag_initialized = False

def initialize_custom_rag():
    """Initialize custom RAG system with vectorstore"""
    global vectorstore, rag_initialized
    
    try:
        vectorstore_path = os.path.join("data", "vectorStores", "store")
        
        if os.path.exists(vectorstore_path):
            vectorstore = FAISS.load_local(
                vectorstore_path, 
                embeddings, 
                allow_dangerous_deserialization=True
            )
            rag_initialized = True
            print("✅ Custom RAG system initialized successfully")
            return True
        else:
            print(f"⚠️  Vectorstore not found at {vectorstore_path}")
            return False
            
    except Exception as e:
        print(f"❌ Error initializing RAG system: {e}")
        return False

# Initialize embeddings and RAG system
embeddings = OpenAIEmbeddings()
rag_initialized = initialize_custom_rag()








# ============ CONVERSATION MANAGEMENT ============
def add_message_to_conversation(session_id, role, message):
    """Add a message to the conversation history"""
    if session_id not in session_conversations:
        session_conversations[session_id] = []
    
    # Add the new message
    session_conversations[session_id].append({
        "role": role,
        "message": message,
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    
    # Keep only the last MAX_CONVERSATION_LENGTH messages
    if len(session_conversations[session_id]) > MAX_CONVERSATION_LENGTH:
        session_conversations[session_id] = session_conversations[session_id][-MAX_CONVERSATION_LENGTH:]

def get_conversation_context(session_id):
    """Get the conversation context for a session"""
    if session_id not in session_conversations:
        return ""
    
    # Format the conversation as a string
    context_parts = []
    for msg in session_conversations[session_id]:
        role = "User" if msg["role"] == "user" else "Assistant"
        context_parts.append(f"{role}: {msg['message']}")
    
    return "\n".join(context_parts)

def init_lead_data(session_id):
    """Initialize lead data structure for a session with updated qualification flow"""
    if session_id not in session_lead_data:
        session_lead_data[session_id] = {
            "in_qualification": False,
            "current_question": "project_description",  # Track current question
            "attempts": 0,  # Track attempts for current question
            "project_description": "",
            "timeline": "",
            "project_type": "",  # personal or company
            "company_name": "",  # only if company project
            "name": "",
            "email": "",
            "complete_description": "",
            "ready_for_save": False
        }

def init_consultation_data(session_id):
    """Initialize consultation data structure for a session"""
    if session_id not in session_consultation_data:
        session_consultation_data[session_id] = {
            "in_consultation": False,
            "current_question": "name",  # Track current question (name -> email -> complete)
            "attempts": 0,  # Track attempts for current question
            "name": "",
            "email": "",
            "consultation_type": "General Consultation",
            "ready_for_save": False
        }

def update_lead_data(session_id, key, value):
    """Update lead data for a session"""
    init_lead_data(session_id)
    session_lead_data[session_id][key] = value

def update_consultation_data(session_id, key, value):
    """Update consultation data for a session"""
    init_consultation_data(session_id)
    session_consultation_data[session_id][key] = value

def get_lead_data(session_id):
    """Get lead data for a session"""
    init_lead_data(session_id)
    return session_lead_data[session_id]

def get_consultation_data(session_id):
    """Get consultation data for a session"""
    init_consultation_data(session_id)
    return session_consultation_data[session_id]

def is_valid_email(email):
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def extract_name_email(user_message):
    """Extract name and email from user message"""
    # Look for email pattern
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    email_match = re.search(email_pattern, user_message)
    
    # Look for name pattern (assuming format like "My name is John" or "I'm John")
    name_patterns = [
        r'(?:my name is|i\'?m|name is|i am)\s+([a-zA-Z\s]+?)(?:\s+and|$|\.|,)',
        r'([a-zA-Z\s]+)\s+(?:is my name|here)',
        r'^([a-zA-Z\s]+?)(?:\s+and|\s+email|\s*$)'
    ]
    
    name = None
    email = email_match.group(0) if email_match else None
    
    for pattern in name_patterns:
        name_match = re.search(pattern, user_message, re.IGNORECASE)
        if name_match:
            name = name_match.group(1).strip()
            # Remove common words and clean up
            name = re.sub(r'\b(my|name|is|and|email)\b', '', name, flags=re.IGNORECASE).strip()
            if name and len(name) > 1:
                break
    
    return name, email

def build_project_description(session_id):
    """Build a complete project description from collected information"""
    lead_data = get_lead_data(session_id)
    
    description_parts = []
    
    # Basic project info
    if lead_data.get("project_description"):
        description_parts.append(f"Project Requirements: {lead_data['project_description']}")
    
    if lead_data.get("timeline"):
        description_parts.append(f"Timeline: {lead_data['timeline']}")
    
    # Project type and company info
    if lead_data.get("project_type"):
        if lead_data["project_type"].lower() == "company":
            description_parts.append(f"Project Type: Company Project")
            if lead_data.get("company_name"):
                description_parts.append(f"Company Name: {lead_data['company_name']}")
        else:
            description_parts.append(f"Project Type: Personal Project")
    
    # Contact info
    if lead_data.get("name"):
        description_parts.append(f"Contact Person: {lead_data['name']}")
    
    # Add conversation context for additional details
    conversation_context = get_conversation_context(session_id)
    description_parts.append(f"\n--- Full Conversation Context ---\n{conversation_context}")
    
    complete_description = "\n".join(description_parts)
    update_lead_data(session_id, "complete_description", complete_description)
    
    return complete_description

















# ============ TOOLS ============\


@tool
def clients_reviews(user_message: str) -> str:
    """Use this tool for client and review-related queries. Returns relevant client or review links in 1-2 lines max."""
    try:
        clients_prompt = PromptTemplate(
            template=f"""You are {COMPANY_NAME}'s professional AI assistant handling client and review queries.
        For client or review-related questions, provide ONLY the appropriate response with relevant link.
        
        STRICT RESPONSE RULES:
        - Maximum 2 lines ONLY
        - NO additional explanations or marketing text
        - Match the exact response format specified below
        
        Response Guidelines:
        - For client inquiries (who are your clients, show me clients, client list, etc.):
          Line 1: "We have diverse clients across the world, you can check it out:"
          Line 2: "Our Clients - https://www.genetechsolutions.com/clients"
        
        - For review/testimonial inquiries (reviews, testimonials, feedback, what clients say, etc.):
          Line 1: "We have so many excellent reviews and love from all over the world, you can see more about reviews in detail in below link:"
          Line 2: "https://www.genetechsolutions.com/testimonials"
        
        User message: {{user_message}}
        
        Match keywords to provide the most relevant response. Look for words like: clients, customers, reviews, testimonials, feedback, ratings, opinions.
        
        IMPORTANT: Keep response to exactly 2 lines maximum. Use the exact phrasing specified above.""",
            input_variables=["user_message"]
        )
        
        clients_chain = clients_prompt | llm
        response = clients_chain.invoke({"user_message": user_message})
        
        if hasattr(response, 'content'):
            return response.content
        else:
            return str(response)
            
    except Exception as e:
        return f"We have diverse clients across the world, you can check it out:\nOur Clients - Genetech Solutions"








@tool
def company_portfolio(user_message: str) -> str:
    """Use this tool for portfolio-related queries. Returns relevant portfolio links in 1-2 lines max."""
    try:
        portfolio_prompt = PromptTemplate(
            template=f"""You are {COMPANY_NAME}'s professional AI assistant handling portfolio queries.
        For portfolio-related questions, provide ONLY the appropriate link with a brief introduction.
        
        STRICT RESPONSE RULES:
        - Maximum 2 lines ONLY
        - Always start with "Sure, here's the link to our [Portfolio Type] portfolio:"
        - Provide the exact link on the second line
        - NO additional explanations or marketing text
        
        Portfolio Links:
        - Web Development: https://genetechsolutions.com/portfolio/web-development.html
        - Mobile Applications: https://www.genetechsolutions.com/portfolio/mobile-apps
        - Personal Branding Websites: https://www.genetechsolutions.com/portfolio/personal-branding-websites
        - LMS Development: https://www.genetechsolutions.com/portfolio/lms
        - E-commerce Solutions/online Shops : https://www.genetechsolutions.com/portfolio/online-shops
        - General Portfolio: https://www.genetechsolutions.com/portfolio
        
        User message: {{user_message}}
        
        Match keywords to provide the most relevant portfolio link. If unsure, default to general portfolio link.
        
        IMPORTANT: Keep response to exactly 2 lines maximum. No exceptions.""",
            input_variables=["user_message"]
        )
        
        portfolio_chain = portfolio_prompt | llm
        response = portfolio_chain.invoke({"user_message": user_message})
        
        if hasattr(response, 'content'):
            return response.content
        else:
            return str(response)
            
    except Exception as e:
        return f"Sure, here's the link to our portfolio:\nhttps://www.genetechsolutions.com/portfolio"








@tool
def handle_greeting_feedbacks(user_message: str) -> str:
    """Use this tool for greetings, feedbacks, thank you messages, and general conversational responses."""
    try:
        greeting_feedback_prompt = PromptTemplate(
            template=f"""You are {COMPANY_NAME}'s friendly AI assistant handling greetings, feedback, and thank you messages.

Guidelines for responses:
- Keep responses concise (1-2 sentences maximum)
- Sound natural and human-like, not robotic
- Be warm and professional
- For greetings: Welcome them and briefly mention how you can help
- For thank you messages: Acknowledge graciously and offer continued assistance
- For feedback: Thank them and show appreciation
- For general conversation: Be friendly and gently guide toward business topics

User message: "{{user_message}}"

Examples of good responses:
User: "hi" → "Hello! Welcome to {COMPANY_NAME}. How can I help you today?"
User: "thank you" → "You're very welcome! Feel free to reach out anytime if you need help to Grow your Business."
User: "thanks a lot" → "My pleasure! I'm here whenever you need assistance."
User: "good morning" → "Good morning! Great to have you here. What can I help you with today?"
User: "that was helpful" → "So glad I could help! Let me know if you have any other questions."

Generate a warm, concise, human-like response:""",
            input_variables=["user_message"]
        )
        
        greeting_chain = greeting_feedback_prompt | llm
        response = greeting_chain.invoke({"user_message": user_message})
        
        if hasattr(response, 'content'):
            return response.content
        else:
            return str(response)
            
    except Exception as e:
        return f"Hello! Welcome to {COMPANY_NAME}. How can I help you today?"
    




    

@tool
def handle_irrelevant_queries(user_message: str) -> str:
    """Use this tool for questions that are not related to Genetech Solutions business."""
    try:
        irrelevant_prompt = PromptTemplate(
            template=f"""You are {COMPANY_NAME}'s professional AI assistant handling off-topic queries.
        For queries NOT directly related to {COMPANY_NAME} or its services:
        - DO NOT use any tools
        - DO NOT give rigid "I cannot help" responses
        - Instead, gently acknowledge their question and smoothly redirect to how our services can benefit their business
        - Always end with a warm invitation to connect or learn more about our solutions
        - Keep responses concise (1  sentences max)
        
        Example approach for irrelevant queries:
        User: "What are ways to make life better?"
        Response: "Rephrased Example:
"While I focus on {COMPANY_NAME}'s services, many of our clients find life easier with reliable tech solutions. We’d love to show you how our development services can streamline your business—would you like to explore this ?"
        
        User message: {{user_message}}
        
        Always maintain a warm, consultative tone that keeps the conversation flowing toward our business solutions, even when redirecting off-topic queries.
        
        IMPORTANT HARD RULES: Keep all responses concise and to the point. Avoid lengthy explanations - aim for 1 sentences maximum that deliver clear value and encourage action.""",


            input_variables=["user_message"]
        )
        
        irrelevant_chain = irrelevant_prompt | llm
        response = irrelevant_chain.invoke({"user_message": user_message})
        
        if hasattr(response, 'content'):
            return response.content
        else:
            return str(response)
            
    except Exception as e:
        return f"While I focus on {COMPANY_NAME}'s services, I'd love to show you how our tech solutions can benefit your business. Let's connect and explore how we can help streamline your operations!"
    



@tool
def start_lead_qualification(user_message: str, session_id: str) -> str:
    """Start the intelligent lead qualification process."""
    init_lead_data(session_id)
    update_lead_data(session_id, "in_qualification", True)
    update_lead_data(session_id, "current_question", "project_description")
    update_lead_data(session_id, "attempts", 0)
    
    return "I'd love to help with your project. To provide the best solution, could you tell me more about your requirements?" 







@tool
def continue_lead_qualification(user_message: str, session_id: str, conversation_context: str) -> str:
    """Continue the intelligent lead qualification process with updated flow."""
    try:
        lead_data = get_lead_data(session_id)
        current_question = lead_data.get("current_question", "project_description")
        attempts = lead_data.get("attempts", 0)
        
        # Check if qualification is already completed
        if lead_data.get("ready_for_save", False):
            # Exit qualification mode
            update_lead_data(session_id, "in_qualification", False)
            return "QUALIFICATION_COMPLETED"
        
        # Create intelligent response based on current question and user input
        qualification_prompt = PromptTemplate(
            template="""You are Genetech Solutions' lead qualification specialist. Your job is to intelligently collect project information through natural, focused conversation.

CURRENT CONTEXT:
- Current Question Focus: {current_question}
- User Response: "{user_message}"
- Conversation History: {conversation_context}
- Previous Attempts: {attempts}
- Current Lead Data: Name="{current_name}", Email="{current_email}"

QUALIFICATION STAGES:
1. "project_description" - What they want to build/achieve
2. "timeline" - When they need it completed
3. "project_type" - Is this for personal use or company
4. "company_name" - Company name (only if project_type is company)
5. "contact_info" - Name and email address
6. "completed" - All information collected

RESPONSE RULES:
1. ANALYZE the user's response to determine if it's:
   - VALID: Contains useful information about the current question
   - INVALID: Too vague, negative, or doesn't answer the question
   - REDIRECT: User is asking something else or going off-topic

2. FOR VALID RESPONSES:
   - Brief acknowledgment (1 sentence max)
   - Move to next question directly
   - Return format: "VALID|next_question|your_response"

3. FOR INVALID RESPONSES:
   - Gentle encouragement with example
   - Stay on the same question but be more specific
   - Return format: "INVALID|same_question|your_response"

4. FOR REDIRECT RESPONSES:
   - Briefly address their concern
   - Redirect back to qualification
   - Return format: "REDIRECT|same_question|your_response"

QUESTION PROGRESSION:
- project_description → timeline
- timeline → project_type
- project_type → company_name (only if company) OR contact_info (if personal)
- company_name → contact_info
- contact_info → completed

CURRENT QUESTION GUIDELINES:

If current_question is "project_description":
- VALID: User describes any project, technology need, or business solution
- INVALID: "no", "yes", "maybe", very vague responses, just greetings
- Ask for: Specific type of project or solution they need

If current_question is "timeline":
- VALID: Any time reference (urgent, flexible, specific dates, months, ASAP, etc.)
- INVALID: "no", "yes", completely unrelated responses
- Ask for: When they want it completed or launched

If current_question is "project_type":
- VALID: Any indication of personal or company project (myself, company, business, personal, etc.)
- INVALID: "no", "yes", unclear responses
- Ask: "Is this project for yourself or are you representing a company?"

If current_question is "company_name":
- VALID: Any company name mentioned
- INVALID: "no", "yes", refusal to provide name
- Ask for: Company name

If current_question is "contact_info":
- SPECIAL LOGIC FOR CONTACT INFO:
  - If BOTH name and email are already collected (current_name and current_email are not empty): VALID|completed|Perfect! I have all the information I need.
  - If user provides BOTH name and email in current message: VALID|completed|Perfect! I have all the information I need.
  - If user provides only name and we don't have email: VALID|contact_info|Thanks! I also need your email address.
  - If user provides only email and we don't have name: VALID|contact_info|Thanks for the email! What's your name?
  - If user provides neither or unclear info: INVALID|contact_info|Please provide your name and email address.

TONE & LENGTH REQUIREMENTS:
- Keep responses to 1-2 sentences maximum
- Sound natural and human, not robotic
- No excessive greetings or fluff
- Stay laser-focused on the current question
- Be direct but friendly

RESPONSE EXAMPLES:

User says "My name is John and email is john@gmail.com":
VALID|completed|Perfect! I have all the information I need about your project.

User says only "John" for contact_info:
VALID|contact_info|Thanks John! What's your email address?

User says only "john@gmail.com" for contact_info:
VALID|contact_info|Thanks for the email! What's your name?

If we already have both name and email from previous messages:
VALID|completed|Perfect! I have all the information I need about your project.

Now analyze this response: "{user_message}"
Current question: {current_question}
Current stored data: Name="{current_name}", Email="{current_email}"
Respond with: STATUS|next_question|your_response""",
            input_variables=["current_question", "user_message", "conversation_context", "attempts", "current_name", "current_email"]
        )
        
        qualification_chain = qualification_prompt | llm
        result = qualification_chain.invoke({
            "current_question": current_question,
            "user_message": user_message,
            "conversation_context": conversation_context,
            "attempts": attempts,
            "current_name": lead_data.get("name", ""),
            "current_email": lead_data.get("email", "")
        })
        
        # Parse the LLM response
        response_text = result.content if hasattr(result, 'content') else str(result)
        parts = response_text.split("|", 2)
        
        if len(parts) != 3:
            # Fallback if parsing fails
            return "I'd love to learn more about your project. Could you share some details about what you're looking to build or achieve?"
        
        status, next_question, bot_response = parts
        status = status.strip().upper()
        next_question = next_question.strip()
        bot_response = bot_response.strip()
        
        # Update lead data based on the response
        if status == "VALID":
            # Store the user's answer based on current question
            if current_question == "project_description":
                update_lead_data(session_id, "project_description", user_message)
            elif current_question == "timeline":
                update_lead_data(session_id, "timeline", user_message)
            elif current_question == "project_type":
                # Determine if it's personal or company
                if any(word in user_message.lower() for word in ["company", "business", "organization", "firm"]):
                    update_lead_data(session_id, "project_type", "company")
                else:
                    update_lead_data(session_id, "project_type", "personal")
            elif current_question == "company_name":
                update_lead_data(session_id, "company_name", user_message)
            elif current_question == "contact_info":
                # Extract name and email from current message
                name, email = extract_name_email(user_message)
                
                # Update only if we extracted new information
                if name and not lead_data.get("name"):
                    update_lead_data(session_id, "name", name)
                if email and not lead_data.get("email"):
                    update_lead_data(session_id, "email", email)
                
                # Check if we now have both pieces of information
                current_name = lead_data.get("name", "")
                current_email = lead_data.get("email", "")
                
                # If we still don't have both, but next_question is completed, force completion
                if next_question == "completed" and (not current_name or not current_email):
                    # Try to extract from the user message again
                    if name:
                        update_lead_data(session_id, "name", name)
                    if email:
                        update_lead_data(session_id, "email", email)
            
            # Update current question and reset attempts
            update_lead_data(session_id, "current_question", next_question)
            update_lead_data(session_id, "attempts", 0)
            
            # Check if we've completed all questions
            if next_question == "completed":
                # Verify we have all required information
                final_lead_data = get_lead_data(session_id)
                if final_lead_data.get("name") and final_lead_data.get("email"):
                    # Build complete project description and mark as ready
                    build_project_description(session_id)
                    update_lead_data(session_id, "ready_for_save", True)
                    # Exit qualification mode
                    update_lead_data(session_id, "in_qualification", False)
                    return "SAVE_LEAD_DATA"
                else:
                    # If we don't have complete info, go back to contact_info
                    update_lead_data(session_id, "current_question", "contact_info")
                    return "I still need your complete contact information. Please provide your name and email address."
                
        elif status == "INVALID":
            # Increment attempts for the same question
            update_lead_data(session_id, "attempts", attempts + 1)
            
        elif status == "REDIRECT":
            # Handle redirect but stay on same question
            update_lead_data(session_id, "attempts", attempts + 1)
        
        return bot_response
        
    except Exception as e:
        print(f"❌ Error in continue_lead_qualification: {str(e)}")
        return "I apologize for the technical issue. Could you please tell me a bit about what kind of project or solution you're looking to build?"




@tool
def start_consultation_request(user_message: str, session_id: str) -> str:
    """Start the consultation request process."""
    init_consultation_data(session_id)
    update_consultation_data(session_id, "in_consultation", True)
    update_consultation_data(session_id, "current_question", "name")
    update_consultation_data(session_id, "attempts", 0)
    
    return "I'd be happy to arrange a consultation for you! To get started, could you please tell me your name?"

@tool
def continue_consultation_request(user_message: str, session_id: str, conversation_context: str) -> str:
    """Continue the consultation request process."""
    try:
        consultation_data = get_consultation_data(session_id)
        current_question = consultation_data.get("current_question", "name")
        attempts = consultation_data.get("attempts", 0)
        
        # Check if consultation is already completed
        if consultation_data.get("ready_for_save", False):
            # Exit consultation mode
            update_consultation_data(session_id, "in_consultation", False)
            return "CONSULTATION_COMPLETED"
        
        # Create intelligent response based on current question and user input
        consultation_prompt = PromptTemplate(
            template="""You are Genetech Solutions' consultation coordinator. Your job is to collect contact information for consultation requests through natural conversation.

CURRENT CONTEXT:
- Current Question Focus: {current_question}
- User Response: "{user_message}"
- Conversation History: {conversation_context}
- Previous Attempts: {attempts}

CONSULTATION STAGES:
1. "name" - Get the user's name
2. "email" - Get the user's email address
3. "completed" - All information collected

RESPONSE RULES:
1. ANALYZE the user's response to determine if it's:
   - VALID: Contains the requested information
   - INVALID: Too vague, doesn't contain the requested info, or invalid format
   - REDIRECT: User is asking something else or going off-topic

2. FOR VALID RESPONSES:
   - Brief acknowledgment (1 sentence max)
   - Move to next question directly
   - Return format: "VALID|next_question|your_response"

3. FOR INVALID RESPONSES:
   - Gentle encouragement asking for the specific information needed
   - Stay on the same question
   - Return format: "INVALID|same_question|your_response"

4. FOR REDIRECT RESPONSES:
   - Briefly address their concern
   - Redirect back to consultation request
   - Return format: "REDIRECT|same_question|your_response"

QUESTION PROGRESSION:
- name → email
- email → completed

CURRENT QUESTION GUIDELINES:

If current_question is "name":
- VALID: User provides any name (first name, full name, etc.)
- INVALID: "no", "yes", unclear responses, no name provided
- Ask for: Their name

If current_question is "email":
- VALID: Message contains a valid email address format
- INVALID: No email provided or invalid email format
- Ask for: Their email address

TONE & LENGTH REQUIREMENTS:
- Keep responses to 1-2 sentences maximum
- Sound natural and human, not robotic
- Be direct but friendly
- Focus on collecting the required information

RESPONSE EXAMPLES:

User says "My name is John":
VALID|email|Thanks John! What's your email address?

User says "john@gmail.com":
VALID|completed|Perfect! Our team will reach out to you shortly for the consultation.

User says only "John" for name:
VALID|email|Thanks John! What's your email address?

User says invalid email "john.com":
INVALID|email|I need a valid email address to arrange the consultation. Could you please provide your email?

Now analyze this response: "{user_message}"
Current question: {current_question}
Respond with: STATUS|next_question|your_response""",
            input_variables=["current_question", "user_message", "conversation_context", "attempts"]
        )
        
        consultation_chain = consultation_prompt | llm
        result = consultation_chain.invoke({
            "current_question": current_question,
            "user_message": user_message,
            "conversation_context": conversation_context,
            "attempts": attempts
        })
        
        # Parse the LLM response
        response_text = result.content if hasattr(result, 'content') else str(result)
        parts = response_text.split("|", 2)
        
        if len(parts) != 3:
            # Fallback if parsing fails
            if current_question == "name":
                return "Could you please tell me your name so I can arrange a consultation for you?"
            else:
                return "Could you please provide your email address so our team can reach out to you?"
        
        status, next_question, bot_response = parts
        status = status.strip().upper()
        next_question = next_question.strip()
        bot_response = bot_response.strip()
        
        # Update consultation data based on the response
        if status == "VALID":
            # Store the user's answer based on current question
            if current_question == "name":
                # Extract name from user message
                name, _ = extract_name_email(user_message)
                if not name:
                    # If extraction fails, use the whole message as name (cleaned up)
                    name = re.sub(r'(my name is|i am|i\'m|name is)', '', user_message, flags=re.IGNORECASE).strip()
                update_consultation_data(session_id, "name", name if name else user_message.strip())
            elif current_question == "email":
                # Extract email from user message
                _, email = extract_name_email(user_message)
                if email and is_valid_email(email):
                    update_consultation_data(session_id, "email", email)
                else:
                    # If no valid email found, this shouldn't be marked as VALID
                    # But if LLM marked it as VALID, there might be a valid email
                    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                    email_match = re.search(email_pattern, user_message)
                    if email_match:
                        update_consultation_data(session_id, "email", email_match.group(0))
            
            # Update current question and reset attempts
            update_consultation_data(session_id, "current_question", next_question)
            update_consultation_data(session_id, "attempts", 0)
            
            # Check if we've completed all questions
            if next_question == "completed":
                # Mark as ready for save
                update_consultation_data(session_id, "ready_for_save", True)
                # Exit consultation mode
                update_consultation_data(session_id, "in_consultation", False)
                return "SAVE_CONSULTATION_DATA"
                
        elif status == "INVALID":
            # Increment attempts for the same question
            update_consultation_data(session_id, "attempts", attempts + 1)
            
        elif status == "REDIRECT":
            # Handle redirect but stay on same question
            update_consultation_data(session_id, "attempts", attempts + 1)
        
        return bot_response
        
    except Exception as e:
        print(f"❌ Error in continue_consultation_request: {str(e)}")
        return "I apologize for the technical issue. Could you please tell me your name so I can arrange a consultation for you?"

@tool
def looking_job_opportunity() -> str:
    """Use this tool when user expresses interest in job opportunities or careers at Genetech Solutions."""
    return "I am happy that you are interested to build your career in Genetech Solutions. Please visit https://www.genetechsolutions.com/jobs to find more interesting vacancies. Apply then our HR team will shortly contact you soon."

@tool
def company_contact_info() -> str:
    """Use this tool when user asks for Specific company contact information "Example  <user_message= Can i get your Contact information > (renamed from contact_info)."""
    try:
        contact_prompt = PromptTemplate(
            template=f"""You are {COMPANY_NAME}'s professional AI assistant providing contact information.

            When users ask for contact information Specific for company {COMPANY_NAME}, provide the following details in a clear, organized bullet point format:

            Company Contact Information:

            • Pakistan Office: +92 21 3455 8425

            • USA Office: +1 734-519-1414

            • General Email: info@genetech.co

            • Direct Consultation with COO Shamim Rajani:

              • Email: shamim@genetech.io

              • LinkedIn: linkedin.com/in/shamimrajani

            
            Present this information in a warm, professional manner and encourage the user to reach out directly for their project needs.
            
            Format your response with:
            1. A friendly opening acknowledging their request
            2. The contact information in clear bullet points
            3. A closing that invites them to get in touch
            
            Keep the tone professional but approachable, and make it clear that the team is ready to help with their project needs.""",
            input_variables=[]
        )
        
        contact_chain = contact_prompt | llm
        response = contact_chain.invoke({})
        
        if hasattr(response, 'content'):
            return response.content
        else:
            return str(response)
            
    except Exception as e:
        # Fallback response if LLM fails
        return f"""Here are the ways to contact {COMPANY_NAME}:

• Pakistan Office: +92 21 3455 8425
• USA Office: +1 734-519-1414
• General Email: info@genetech.co
• Direct Consultation with COO Shamim Rajani:
  • Email: shamim@genetech.io
  • LinkedIn: linkedin.com/in/shamimrajani

Feel free to reach out through any of these channels - our team is ready to help with your project needs!"""

@tool
def search_company_info(question: str) -> str:
    """Unified RAG tool to search company information using vectorstore."""
    global vectorstore, rag_initialized
    
    if not rag_initialized or vectorstore is None:
        return f"I apologize, but I'm currently unable to access our company database. Please contact our team directly for detailed information about our services at info@{COMPANY_NAME.lower().replace(' ', '')}.com"
    
    try:
        docs = vectorstore.similarity_search(question, k=10)
        
        if not docs:
            return f"Thanks for your interest in {COMPANY_NAME}! I don't have specific information about that topic in our database right now. I'd recommend reaching out to our team directly at info@{COMPANY_NAME.lower().replace(' ', '')}.com - they'll be able to give you detailed answers and discuss how we can help with your specific needs!"
        
        context = "\n\n".join([doc.page_content for doc in docs])
        
        prompt = PromptTemplate(
            template=f"""You are {COMPANY_NAME}'s professional AI assistant. Respond to customer inquiries with warmth, expertise, and a gentle nudge toward action.
            Guidelines for your responses:
            - Keep answers concise and conversational (1 sentences max)
            - Use a warm, human-like tone that builds trust
            - Focus on benefits and value to the customer
            - Always include a soft call-to-action that moves toward a decision
            - If information isn't in the context, politely redirect to direct contact
            Response format:
            - Provide a clear short concise, helpful answer
            - End with a gentle invitation to take the next step
            Context:
            {{context}}
            Customer Question:
            {{question}}
            Response:""",
            input_variables=["context", "question"]
        )
        
        rag_chain = prompt | llm
        response = rag_chain.invoke({"context": context, "question": question})
        
        if hasattr(response, 'content'):
            return response.content
        else:
            return str(response)
        
    except Exception as e:
        print(f"❌ Error in search_company_info: {str(e)}")
        return f"Thanks for your interest in {COMPANY_NAME}! I'm having a small technical hiccup right now. Please reach out to our team directly at info@{COMPANY_NAME.lower().replace(' ', '')}.com and they'll be happy to answer your questions and discuss your project needs!"

# ============ AGENTS ============
# ============ AGENTS ============
intent_classifier_agent = Agent(
    role='Intent Classification Specialist',
    goal='Accurately classify user intent for proper query routing',
    backstory="""You are an expert at understanding user intent and classifying queries. 
    You analyze user messages and determine their primary purpose to ensure they get routed 
    to the right tool for the best response.
    
    IMPORTANT: Always respond with ONLY the classification category name. No JSON, no extra text, no explanations.""",
    llm=llm,
    verbose=False,
    allow_delegation=False,
    max_iter=5
)

query_router_agent = Agent(
    role='Query Router and Conversation Manager',
    goal='Route user queries and manage intelligent lead qualification and consultation request conversations',
    backstory=f"""You are an intelligent query router and conversation manager for {COMPANY_NAME} website. 
    
    Your responsibilities:
    1. Route queries to appropriate tools based on classified intent
    2. Manage the intelligent lead qualification process
    3. Manage the consultation request process
    4. Ensure smooth conversation flow from interest to contact information collection
    
    ROUTING LOGIC:
    - greeting_feedback → handle_greeting_feedbacks (for greetings, thanks, feedback)
    - business_interest → start_lead_qualification (if not in qualification) OR continue_lead_qualification (if in qualification)
    - consultation_request → start_consultation_request (if not in consultation) OR continue_consultation_request (if in consultation)
    - company_info → search_company_info
    - job_opportunity → looking_job_opportunity
    - company_contact_info → company_contact_info (if user asks for specific contact information Specific For Genetech Solutions (Example "What is your Contact Information?", "What is your Company Email?" "What is your Company Phone Number") is any "your", "Company", "Genetech solutions" in Question Identify that user is asking contact information Specific to Genetech)
    - portfolio_request → company_portfolio (for portfolio-related queries)
    - clients_reviews → clients_reviews (for client list or review/testimonial queries)
    - irrelevant → handle_irrelevant_queries
    
    UPDATED LEAD QUALIFICATION FLOW:
    1. Project Description
    2. Timeline
    3. Project Type (Personal/Company)
    4. Company Name (if Company project)
    5. Contact Info (Name + Email)
    
    CONSULTATION REQUEST FLOW:
    1. Name
    2. Email
    
    CRITICAL: Always return EXACTLY what the tool outputs. No JSON formatting, no extra text, no "Final Answer" wrapper.""",
    tools=[handle_greeting_feedbacks, start_lead_qualification, continue_lead_qualification, start_consultation_request, continue_consultation_request, looking_job_opportunity, company_contact_info, search_company_info, handle_irrelevant_queries, company_portfolio, clients_reviews],
    llm=llm,
    verbose=False,
    allow_delegation=False,
    max_iter=5
)

# ============ HELPER FUNCTIONS ============
def get_or_create_session_id():
    """Get or create a session ID for the user"""
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    return session['session_id']

def get_or_create_crew(session_id):
    """Get or create a crew for the session without memory for maximum performance"""
    # Update last activity time
    session_last_activity[session_id] = time.time()
    
    if session_id not in session_crews:
        # Create crew with no memory for maximum performance
        crew = Crew(
            agents=[intent_classifier_agent, query_router_agent],
            tasks=[],
            process=Process.sequential,
            memory=False,  # Disable all memory for maximum performance
            verbose=False,  # Disable verbose to reduce JSON output
            output_log_file=False,  # Disable output logging
            step_callback=None  # Disable step callbacks
        )
        
        session_crews[session_id] = crew
        print(f"✅ Created new crew for session {session_id} with no memory (using custom context management)")
    
    return session_crews[session_id]

def cleanup_old_sessions():
    """Clean up old sessions to free memory"""
    current_time = time.time()
    sessions_to_remove = []
    
    for session_id, last_activity in session_last_activity.items():
        if current_time - last_activity > SESSION_TIMEOUT:
            sessions_to_remove.append(session_id)
    
    for session_id in sessions_to_remove:
        if session_id in session_crews:
            del session_crews[session_id]
        if session_id in session_last_activity:
            del session_last_activity[session_id]
        if session_id in session_conversations:
            del session_conversations[session_id]
        if session_id in session_lead_data:
            del session_lead_data[session_id]
        if session_id in session_consultation_data:
            del session_consultation_data[session_id]
        print(f"🧹 Cleaned up session {session_id}")

def start_cleanup_thread():
    """Start a background thread to clean up old sessions"""
    def cleanup():
        while True:
            time.sleep(300)  # Check every 5 minutes
            cleanup_old_sessions()
    
    thread = threading.Thread(target=cleanup, daemon=True)
    thread.start()

def classify_query_intent(user_input: str, crew, conversation_context: str, session_id: str) -> str:
    """Enhanced LLM-based intent classification that understands qualification and consultation context"""
    
    # Check if user is already in lead qualification
    lead_data = get_lead_data(session_id)
    if lead_data.get("in_qualification", False) and not lead_data.get("ready_for_save", False):
        # Always return business_interest if in qualification to continue the process
        return "business_interest"
    
    # Check if user is already in consultation request
    consultation_data = get_consultation_data(session_id)
    if consultation_data.get("in_consultation", False) and not consultation_data.get("ready_for_save", False):
        # Always return consultation_request if in consultation to continue the process
        return "consultation_request"
    
    # Create a prompt that includes conversation context
    context_prompt = ""
    if conversation_context:
        context_prompt = f"""
        CONVERSATION CONTEXT:
        {conversation_context}
        
        """
    
    classification_task = Task(
        description=f"""{context_prompt}
        You are an expert intent classifier for Genetech Solutions FAQ bot. Your job is to analyze user messages and classify them accurately.
        
        USER MESSAGE: "{user_input}"
        
        CLASSIFICATION CATEGORIES:
        1. "greeting_feedback" - Simple greetings ("hi", "hello", "hey"), thank you messages ("thanks", "thank you"), feedback responses ("that's helpful", "great"), or general conversational responses
        2. "business_interest" - User wants to engage Genetech Solutions for work or shows commercial intent for project development example "can you develop a website for me"
        3. "consultation_request" - User specifically asks for consultation, wants to contact someone, asks "how do I contact","how do i contact you", "can I get a quick consultation", "how can I contact you", wants to speak with team
        4. "company_info" - Questions specifically about Genetech Solutions' services, team, processes, pricing that require company knowledge Example "do you provide Cybersecurity services?"
        5. "job_opportunity" - User expressing interest in jobs, careers, or employment and Hiring Process at Genetech Solutions
        6. "company_contact_info" - User asking for general company contact information, phone numbers, email addresses, "can you give me contact info of company/Genetech", "How can I contact your company" "What is your Company/your Email?" "What is your Company/your Phone Number?"
        7. "portfolio_request" - User asking for portfolio-related information or links or Projects you have done Example "What projects you have done", "can you show me your web Development portfolio", "Great, can you show me some examples of websites developed?" "What type of Apps can you make?", "can you show me your portfolio"
        8. "clients_reviews" - User asking about clients, client list, reviews, testimonials, feedback from customers Example "Who are your clients?", "Show me your clients", "What do your clients say?", "Can I see reviews?", "Show me testimonials", "Customer feedback"
        9. "irrelevant" - General knowledge questions not specific to Genetech Solutions

        CRITICAL DISTINCTIONS:
        - "consultation_request" = User wants to talk/consult with someone (personal consultation) or say "How i contact you"
        - "company_contact_info" = User wants Specific company contact details (informational)
        - "business_interest" = User wants to hire for specific project work or want to develop a project with Genetech Solutions
        - "portfolio_request" = User wants to see examples of work/projects done
        - "clients_reviews" = User wants to see client list or customer reviews/testimonials

        CRITICAL: If the user is answering questions about their project needs, requirements, or business intentions, classify as "business_interest" even if their answer seems negative or unclear.
        
        Think through your reasoning, then respond with ONLY the category name: greeting_feedback, business_interest, consultation_request, company_info, job_opportunity, company_contact_info, portfolio_request, clients_reviews, or irrelevant
        """,
        expected_output="Single category name: greeting_feedback, business_interest, consultation_request, company_info, job_opportunity, company_contact_info, portfolio_request, clients_reviews, or irrelevant",
        agent=intent_classifier_agent
    )
    
    try:
        # Set the task to the crew
        crew.tasks = [classification_task]
        
        # Run the crew
        result = crew.kickoff()
        intent = str(result).strip().lower()
        
        # Add logging
        print(f"🔍 Message: '{user_input}' → Classified as: {intent}")

        valid_intents = ['greeting_feedback', 'business_interest', 'consultation_request', 'company_info', 'job_opportunity', 'company_contact_info', 'portfolio_request', 'clients_reviews', 'irrelevant','clients_reviews']
        if intent in valid_intents:
            return intent
        else:
            return 'company_info'
            
    except Exception as e:
        print(f"Intent classification error: {e}")
        return 'company_info'

def create_query_routing_task(user_message: str, intent: str, session_id: str, conversation_context: str):
    """Create task for routing user queries based on classified intent"""
    
    lead_data = get_lead_data(session_id)
    consultation_data = get_consultation_data(session_id)
    
    return Task(
        description=f"""
        The user message "{user_message}" has been classified with intent: "{intent}"
        Session ID: {session_id}
        User in qualification: {lead_data["in_qualification"]}
        User in consultation: {consultation_data["in_consultation"]}
        Current question: {lead_data.get("current_question", "N/A")}
        Consultation question: {consultation_data.get("current_question", "N/A")}
        Attempts: {lead_data.get("attempts", 0)}
        Ready for save: {lead_data["ready_for_save"]}
        
        CONVERSATION CONTEXT:
        {conversation_context}
        
        Route this query to the appropriate tool based on the intent and session state:
        
        FOR BUSINESS INTEREST:
        - If user is NOT in qualification process: use start_lead_qualification tool with user_message and session_id
        - If user IS in qualification process: use continue_lead_qualification tool with user_message, session_id, and conversation_context
        
        FOR CONSULTATION REQUEST:
        - If user is NOT in consultation process: use start_consultation_request tool with user_message and session_id
        - If user IS in consultation process: use continue_consultation_request tool with user_message, session_id, and conversation_context
        
        FOR OTHER INTENTS:
        - If intent is "greeting_feedback" → use handle_greeting_feedbacks tool with the user message
        - If intent is "company_info" → use search_company_info tool with the user message as the question parameter
        - If intent is "job_opportunity" → use looking_job_opportunity tool
        - If intent is "company_contact_info" → use company_contact_info tool
        - If intent is "portfolio_request" → use company_portfolio tool with the user message
        - If intent is "clients_reviews" → use clients_reviews tool with the user message
        - If intent is "irrelevant" → use handle_irrelevant_queries tool with the user message

        IMPORTANT:
        - For qualification and consultation tools, pass the required parameters (user_message, session_id, conversation_context)
        - For portfolio requests, use company_portfolio tool with user_message parameter
        - For clients/reviews requests, use clients_reviews tool with user_message parameter
        - Return EXACTLY what the chosen tool outputs
        - Do not modify or add to the tool output
        """,
        expected_output="Exact tool output based on intent classification and session state",
        agent=query_router_agent
    )

def save_lead_to_database(session_id: str):
    """Save lead information to SQLite database using collected data."""
    try:
        lead_data = get_lead_data(session_id)
        
        # Validate required fields
        required_fields = ["name", "email", "project_description", "timeline"]
        for field in required_fields:
            if not lead_data.get(field):
                return False, f"❌ Missing required field: {field}"
        
        # Validate email format
        if not is_valid_email(lead_data["email"]):
            return False, f"❌ Invalid email format: {lead_data['email']}"
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get full conversation for context
        full_conversation = get_conversation_context(session_id)
        
        # Insert the new lead with updated schema
        cursor.execute("""
            INSERT INTO leads (
                date, name, email, company_name, project_description,
                timeline, project_type, status, full_conversation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            lead_data["name"],
            lead_data["email"],
            lead_data.get("company_name", ""),
            lead_data["complete_description"],
            lead_data["timeline"],
            lead_data.get("project_type", "personal"),
            'New Lead',
            full_conversation
        ))
        
        conn.commit()
        conn.close()
        
        # Clean up lead data after successful save
        # Remove the session_lead_data for this session
        if session_id in session_lead_data:
            del session_lead_data[session_id]
        init_lead_data(session_id)
        update_lead_data(session_id, "in_qualification", False)
        update_lead_data(session_id, "ready_for_save", False)
        update_lead_data(session_id, "current_question", "project_description")
        update_lead_data(session_id, "attempts", 0)
        update_lead_data(session_id, "name", "")
        update_lead_data(session_id, "email", "")
        
        return True, f"✅ Lead saved successfully to database"
        
    except Exception as e:
        print(f"❌ Error saving lead to database: {e}")
        return False, f"❌ Error saving lead: {str(e)}"

def save_consultation_to_database(session_id: str):
    """Save consultation request information to SQLite database using collected data."""
    try:
        consultation_data = get_consultation_data(session_id)
        
        # Validate required fields
        required_fields = ["name", "email"]
        for field in required_fields:
            if not consultation_data.get(field):
                return False, f"❌ Missing required field: {field}"
        
        # Validate email format
        if not is_valid_email(consultation_data["email"]):
            return False, f"❌ Invalid email format: {consultation_data['email']}"
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get full conversation for context
        full_conversation = get_conversation_context(session_id)
        
        # Insert the new consultation request
        cursor.execute("""
            INSERT INTO consultant (
                date, name, email, consultation_type, status, full_conversation
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            consultation_data["name"],
            consultation_data["email"],
            consultation_data.get("consultation_type", "General Consultation"),
            'New Request',
            full_conversation
        ))
        
        conn.commit()
        conn.close()
        
        # Clean up consultation data after successful save
        update_consultation_data(session_id, "in_consultation", False)
        update_consultation_data(session_id, "ready_for_save", False)
        update_consultation_data(session_id, "current_question", "name")
        update_consultation_data(session_id, "attempts", 0)
        
        return True, f"✅ Consultation request saved successfully to database"
        
    except Exception as e:
        print(f"❌ Error saving consultation to database: {e}")
        return False, f"❌ Error saving consultation: {str(e)}"

def process_user_message(user_input: str, crew, session_id: str):
    """Process user message using LLM-based intent classification with intelligent lead qualification and consultation requests"""
    try:
        # Skip processing for session initialization
        if user_input == '_init_session_':
            return {
                "response": "Session initialized",
                "collect_lead": False
            }
        
        # Get conversation context
        conversation_context = get_conversation_context(session_id)
        
        # Add user message to conversation history
        add_message_to_conversation(session_id, "user", user_input)
            
        # Step 1: Classify user intent using LLM with conversation context
        intent = classify_query_intent(user_input, crew, conversation_context, session_id)
        
        # Step 2: Create and run query routing task based on classified intent
        routing_task = create_query_routing_task(user_input, intent, session_id, conversation_context)
        
        # Set the task to the crew
        crew.tasks = [routing_task]
        
        # Run the crew
        result = crew.kickoff()
        
        # Extract clean response from result
        if hasattr(result, 'raw'):
            response = str(result.raw).strip()
        else:
            response = str(result).strip()
        
        # Clean up JSON formatting if it exists
        if response.startswith('```json') and response.endswith('```'):
            # Extract content between json markers
            import json
            try:
                json_content = response[7:-3].strip()  # Remove ```json and ```
                parsed = json.loads(json_content)
                if 'Final Answer' in parsed:
                    response = parsed['Final Answer']
                elif 'final_answer' in parsed:
                    response = parsed['final_answer']
                elif 'answer' in parsed:
                    response = parsed['answer']
                else:
                    # Take the last value in the JSON
                    response = list(parsed.values())[-1]
            except:
                # If JSON parsing fails, try to extract manually
                lines = response.split('\n')
                for line in lines:
                    if '"Final Answer"' in line or '"final_answer"' in line:
                        response = line.split(':', 1)[1].strip().strip('"').strip(',')
                        break
        
        # Remove any remaining JSON formatting
        if response.startswith('{') and response.endswith('}'):
            try:
                import json
                parsed = json.loads(response)
                if 'Final Answer' in parsed:
                    response = parsed['Final Answer']
                elif 'final_answer' in parsed:
                    response = parsed['final_answer']
                else:
                    response = list(parsed.values())[-1]
            except:
                pass
        
        # Clean up any remaining quotes or formatting
        response = response.strip('"').strip("'").strip()
        
        # Add logging
        print(f"🔧 Tool called for intent '{intent}' returned: {response}")
        
        # Check if we need to save lead data
        if response == "SAVE_LEAD_DATA":
            # Save the lead data to database
            success, message = save_lead_to_database(session_id)
            
            if success:
                final_response = "Perfect! I have all the information I need about your project. Our team will contact you shortly with a detailed proposal."
                # Add bot response to conversation history
                add_message_to_conversation(session_id, "bot", final_response)
                
                print("💾 Lead data saved successfully to database")
                return {
                    "response": final_response,
                    "collect_lead": False
                }
            else:
                error_response = f"I apologize, but there was an issue saving your information. Please try again or contact our team directly."
                add_message_to_conversation(session_id, "bot", error_response)
                
                return {
                    "response": error_response,
                    "collect_lead": False
                }
        
        # Check if we need to save consultation data
        elif response == "SAVE_CONSULTATION_DATA":
            # Save the consultation data to database
            success, message = save_consultation_to_database(session_id)
            
            if success:
                final_response = """Perfect! Our team will reach out to you shortly for the consultation. 

For direct consultation, you can also contact our COO Shamim Rajani:
• Email: shamim@genetech.io
• LinkedIn: linkedin.com/in/shamimrajani"""
                # Add bot response to conversation history
                add_message_to_conversation(session_id, "bot", final_response)
                
                print("💾 Consultation request saved successfully to database")
                return {
                    "response": final_response,
                    "collect_lead": False
                }
            else:
                error_response = f"I apologize, but there was an issue saving your consultation request. Please try again or contact our team directly."
                add_message_to_conversation(session_id, "bot", error_response)
                
                return {
                    "response": error_response,
                    "collect_lead": False
                }
        
        # Check if qualification or consultation completed (tool exited)
        elif response in ["QUALIFICATION_COMPLETED", "CONSULTATION_COMPLETED"]:
            # Handle post-qualification/consultation messages with greeting/feedback tool
            final_response = "You're very welcome! Feel free to reach out anytime if you need help with your project or consultation."
            add_message_to_conversation(session_id, "bot", final_response)
            
            return {
                "response": final_response,
                "collect_lead": False
            }
        else:
            # Add bot response to conversation history
            add_message_to_conversation(session_id, "bot", response)
            
            return {
                "response": response,
                "collect_lead": False
            }
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return {
            "response": "I apologize for the technical issue. Please try asking your question again, or contact our team directly for assistance.",
            "collect_lead": False
        }

# ============ FLASK ROUTES ============
@app.route('/')
def index():
    """Serve the frontend HTML"""
    return render_template('interface.html')

@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve static files like the logo"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/chat', methods=['POST'])
def chat():
    """Process chat messages and return bot response"""
    try:
        data = request.json
        user_message = data.get('message', '')
        
        if not user_message:
            return jsonify({"error": "No message provided"}), 400
        
        # Get or create session ID
        session_id = get_or_create_session_id()
        
        # Get or create crew for this session
        crew = get_or_create_crew(session_id)
        
        # Process the message with the crew
        result = process_user_message(user_message, crew, session_id)
        
        return jsonify(result)
    
    except Exception as e:
        print(f"Error in /chat endpoint: {str(e)}")
        return jsonify({"error": "An error occurred while processing your request"}), 500

@app.route('/save_lead', methods=['POST'])
def save_lead():
    """Legacy endpoint - now handled automatically in chat flow"""
    try:
        return jsonify({
            "success": True, 
            "message": "Lead saving is now handled automatically in the chat flow."
        })
    
    except Exception as e:
        print(f"Error in /save_lead endpoint: {str(e)}")
        return jsonify({"success": False, "message": "An error occurred"}), 500

@app.route('/leads', methods=['GET'])
def view_leads():
    """View all leads in the database (for admin purposes)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, date, name, email, company_name, project_type, 
                   timeline, status, project_description 
            FROM leads 
            ORDER BY date DESC
        """)
        
        leads = cursor.fetchall()
        conn.close()
        
        # Convert to list of dictionaries
        leads_list = []
        for lead in leads:
            leads_list.append({
                "id": lead["id"],
                "date": lead["date"],
                "name": lead["name"],
                "email": lead["email"],
                "company_name": lead["company_name"],
                "project_type": lead["project_type"],
                "timeline": lead["timeline"],
                "status": lead["status"],
                "project_description": lead["project_description"][:200] + "..." if len(lead["project_description"]) > 200 else lead["project_description"]
            })
        
        return jsonify({
            "success": True,
            "leads": leads_list,
            "count": len(leads_list)
        })
        
    except Exception as e:
        print(f"Error in /leads endpoint: {str(e)}")
        return jsonify({"success": False, "message": "Error retrieving leads"}), 500

@app.route('/consultations', methods=['GET'])
def view_consultations():
    """View all consultation requests in the database (for admin purposes)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, date, name, email, consultation_type, status
            FROM consultant 
            ORDER BY date DESC
        """)
        
        consultations = cursor.fetchall()
        conn.close()
        
        # Convert to list of dictionaries
        consultations_list = []
        for consultation in consultations:
            consultations_list.append({
                "id": consultation["id"],
                "date": consultation["date"],
                "name": consultation["name"],
                "email": consultation["email"],
                "consultation_type": consultation["consultation_type"],
                "status": consultation["status"]
            })
        
        return jsonify({
            "success": True,
            "consultations": consultations_list,
            "count": len(consultations_list)
        })
        
    except Exception as e:
        print(f"Error in /consultations endpoint: {str(e)}")
        return jsonify({"success": False, "message": "Error retrieving consultations"}), 500

# ============ RUN THE APP ============
if __name__ == "__main__":
    # Check RAG initialization status
    if rag_initialized:
        print("✅ Custom RAG system ready")
    else:
        print("⚠️  Warning: Custom RAG system not fully initialized. Some features may be limited.")
    
    # Check if vectorstore exists
    vectorstore_path = os.path.join("data", "vectorStores", "store")
    if not os.path.exists(vectorstore_path):
        print("⚠️  Warning: Vectorstore not found at 'data/vectorstore'. Please ensure your vectorstore is properly configured.")
    
    # Create necessary directories
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    # Start the cleanup thread
    start_cleanup_thread()
    
    print("🧠 Using UPDATED INTELLIGENT lead qualification and consultation request system")
    print("🔄 Lead Flow: Project Description → Timeline → Project Type → Company Name (if company) → Contact Info (Name + Email)")
    print("📞 Consultation Flow: Name → Email")
    print("💾 Features: Automatic data extraction, email validation, direct database saving")
    print("🎯 No forms required - all data collected through natural conversation")
    print("✅ NEW: Added consultation request tool")
    # Run the Flask app
    app.run(debug=True, host='0.0.0.0', port=5000)