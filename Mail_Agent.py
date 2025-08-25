from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import sqlite3
import os
import uuid
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd
import io
import base64
from crewai import Agent, Task, Crew, Process
from crewai.tools import tool
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')
CORS(app)

# Database path
DATABASE_PATH = 'leads.db'

# Email configuration
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'noreply@genetechsolutions.com')

# ============ DATABASE INITIALIZATION ============
def init_database():
    """Initialize the SQLite database with leads table"""
    if not os.path.exists(DATABASE_PATH):
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Create leads table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                project_description TEXT NOT NULL,
                status TEXT DEFAULT 'New Lead',
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        print(f"Database initialized at {DATABASE_PATH}")
    else:
        print(f"Database already exists at {DATABASE_PATH}")

# Initialize database on startup
init_database()

# ============ LLM SETUP ============
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.2,
    max_tokens=1000
)

# ============ DATABASE FUNCTIONS ============
def get_db_connection():
    """Create a database connection to the SQLite database"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_all_leads():
    """Get all leads from the database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM leads ORDER BY date DESC")
        leads = cursor.fetchall()
        conn.close()
        return [dict(lead) for lead in leads]
    except Exception as e:
        print(f"Error fetching leads: {e}")
        return []

def get_lead_by_id(lead_id):
    """Get a specific lead by ID"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM leads WHERE id = ?", (lead_id,))
        lead = cursor.fetchone()
        conn.close()
        return dict(lead) if lead else None
    except Exception as e:
        print(f"Error fetching lead: {e}")
        return None

def create_lead(email, project_description, status='New Lead'):
    """Create a new lead in the database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO leads (email, project_description, status) VALUES (?, ?, ?)",
            (email, project_description, status)
        )
        lead_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return lead_id
    except Exception as e:
        print(f"Error creating lead: {e}")
        return None

def update_lead_status(lead_id, status):
    """Update the status of a lead"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error updating lead status: {e}")
        return False

def delete_lead(lead_id):
    """Delete a lead from the database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error deleting lead: {e}")
        return False

def get_lead_statistics():
    """Get statistics about leads"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Total leads
        cursor.execute("SELECT COUNT(*) as total FROM leads")
        total_leads = cursor.fetchone()['total']
        
        # Leads by status
        cursor.execute("SELECT status, COUNT(*) as count FROM leads GROUP BY status")
        status_counts = cursor.fetchall()
        
        # Leads by day (last 7 days)
        cursor.execute("""
            SELECT DATE(date) as day, COUNT(*) as count
            FROM leads
            WHERE date >= datetime('now', '-7 days')
            GROUP BY DATE(date)
            ORDER BY day
        """)
        daily_counts = cursor.fetchall()
        
        conn.close()
        
        return {
            'total_leads': total_leads,
            'status_counts': [dict(item) for item in status_counts],
            'daily_counts': [dict(item) for item in daily_counts]
        }
    except Exception as e:
        print(f"Error fetching statistics: {e}")
        return {
            'total_leads': 0,
            'status_counts': [],
            'daily_counts': []
        }

# ============ EMAIL TOOLS ============
@tool
def generate_email_content(recipient_email: str, project_description: str, context: str = "") -> str:
    """Generate personalized email content for a lead using CrewAI."""
    try:
        email_prompt = PromptTemplate(
            template=f"""
You are a professional email writer for Genetech Solutions, a leading software development company specializing in custom solutions, mobile apps, web development, and AI integration.
Your task is to craft a personalized email to a potential client who has expressed interest in our services.

RECIPIENT EMAIL: {recipient_email}
PROJECT DESCRIPTION: {project_description}
CONTEXT: {context}

Guidelines for the email:
- Use a professional, friendly, and consultative tone that reflects Genetech Solutions' brand
- Personalize the email by referencing their specific project description
- Highlight how our services can address their specific needs
- Include a clear call-to-action for the next steps
- Keep the email concise (3-4 paragraphs) but informative
- Format the email with a subject line, greeting, body, and closing
- Do not include placeholders like [Name] or [Company]

The email should be formatted as follows:
Subject: [Appropriate subject line]

Dear [Appropriate salutation],

[Email body]

Best regards,
[Your name]
Genetech Solutions Team
""",
            input_variables=["recipient_email", "project_description", "context"]
        )
        
        email_chain = email_prompt | llm
        response = email_chain.invoke({
            "recipient_email": recipient_email,
            "project_description": project_description,
            "context": context
        })
        
        if hasattr(response, 'content'):
            return response.content
        else:
            return str(response)
    except Exception as e:
        print(f"Error generating email content: {e}")
        return f"""Subject: Following up on your inquiry with Genetech Solutions

Dear Valued Client,

Thank you for your interest in Genetech Solutions. Based on your project description: {project_description}, we believe our team can help you achieve your goals.

We specialize in custom software development, mobile apps, web solutions, and AI integration. Our experts would be delighted to discuss your project in more detail.

Please let us know a convenient time for a call or meeting.

Best regards,
Genetech Solutions Team"""

@tool
def send_email(recipient_email: str, subject: str, body: str) -> str:
    """Send an email to a recipient."""
    try:
        # Create message container
        msg = MIMEMultipart()
        msg['From'] = FROM_EMAIL
        msg['To'] = recipient_email
        msg['Subject'] = subject
        
        # Attach the body
        msg.attach(MIMEText(body, 'plain'))
        
        # Connect to SMTP server and send email
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        text = msg.as_string()
        server.sendmail(FROM_EMAIL, recipient_email, text)
        server.quit()
        
        return f"Email successfully sent to {recipient_email}"
    except Exception as e:
        print(f"Error sending email: {e}")
        return f"Failed to send email: {str(e)}"

# ============ AGENTS ============
email_writer_agent = Agent(
    role='Professional Email Writer',
    goal='Craft personalized and professional emails for Genetech Solutions',
    backstory="""You are an expert email writer for Genetech Solutions, a leading software development company.
You specialize in creating personalized, professional emails that reflect the company's brand and values.
Your emails are always tailored to the specific needs of each client and highlight how Genetech Solutions can help them achieve their goals.
You maintain a consultative tone that builds trust and encourages action.""",
    llm=llm,
    verbose=False
)

email_sender_agent = Agent(
    role='Email Sender',
    goal='Send emails to clients using SMTP',
    backstory="""You are responsible for sending emails to clients through the company's SMTP server.
You ensure that emails are delivered successfully and provide confirmation of delivery.
You handle any technical issues that might arise during the email sending process.""",
    tools=[send_email],
    llm=llm,
    verbose=False
)

# ============ HELPER FUNCTIONS ============
def create_email_generation_task(recipient_email: str, project_description: str, context: str = ""):
    """Create a task for generating email content"""
    return Task(
        description=f"""
Generate a personalized email for a potential client.
Recipient Email: {recipient_email}
Project Description: {project_description}
Context: {context}

The email should be professional, friendly, and tailored to the specific project description.
Include a subject line, greeting, body, and closing.
""",
        expected_output="A complete email with subject line and body",
        agent=email_writer_agent
    )

def create_email_sending_task(recipient_email: str, subject: str, body: str):
    """Create a task for sending an email"""
    return Task(
        description=f"""
Send an email to the recipient.
Recipient Email: {recipient_email}
Subject: {subject}
Body: {body}

Use the send_email tool to send this email through the company's SMTP server.
""",
        expected_output="Confirmation of email delivery",
        agent=email_sender_agent
    )

def generate_email_content_only(recipient_email: str, project_description: str, context: str = ""):
    """Generate email content using CrewAI agents (without sending)"""
    try:
        # Create crew
        crew = Crew(
            agents=[email_writer_agent],
            tasks=[],
            process=Process.sequential,
            verbose=True
        )
        
        # Generate email content
        email_task = create_email_generation_task(recipient_email, project_description, context)
        crew.tasks = [email_task]
        email_result = crew.kickoff()
        
        # Extract subject and body from the generated email
        email_content = str(email_result).strip()
        
        # Parse the email to extract subject and body
        lines = email_content.split('\n')
        subject = ""
        body_lines = []
        
        # Extract subject
        for i, line in enumerate(lines):
            if line.startswith("Subject:"):
                subject = line.replace("Subject:", "").strip()
                # The rest of the lines after the subject line are the body
                body_lines = lines[i+1:]
                break
        
        # If no subject found, create a default one
        if not subject:
            subject = "Following up on your inquiry with Genetech Solutions"
            body_lines = lines
        
        # Join body lines, skipping empty lines at the beginning
        body = '\n'.join(body_lines).strip()
        
        return {
            "success": True,
            "subject": subject,
            "body": body
        }
    except Exception as e:
        print(f"Error generating email content: {e}")
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }

def send_email_only(recipient_email: str, subject: str, body: str):
    """Send an email using CrewAI agents"""
    try:
        # Create crew
        crew = Crew(
            agents=[email_sender_agent],
            tasks=[],
            process=Process.sequential,
            verbose=True
        )
        
        # Send the email
        send_task = create_email_sending_task(recipient_email, subject, body)
        crew.tasks = [send_task]
        send_result = crew.kickoff()
        
        return {
            "success": True,
            "message": str(send_result)
        }
    except Exception as e:
        print(f"Error sending email: {e}")
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }

def prepare_status_chart_data(status_counts):
    """Prepare status chart data for Chart.js"""
    if not status_counts:
        return {
            "labels": [],
            "values": []
        }
    
    labels = [item['status'] for item in status_counts]
    values = [item['count'] for item in status_counts]
    
    return {
        "labels": labels,
        "values": values
    }

def prepare_daily_chart_data(daily_counts):
    """Prepare daily chart data for Chart.js"""
    if not daily_counts:
        return {
            "labels": [],
            "values": []
        }
    
    # Format dates for display
    labels = []
    for item in daily_counts:
        date_str = item['day']
        # Convert from YYYY-MM-DD to a more readable format
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        labels.append(date_obj.strftime('%m/%d'))
    
    values = [item['count'] for item in daily_counts]
    
    return {
        "labels": labels,
        "values": values
    }

# ============ FLASK ROUTES ============
@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index_MAIL.html')

@app.route('/api/leads')
def get_leads():
    """API endpoint to get all leads"""
    leads = get_all_leads()
    return jsonify(leads)

@app.route('/api/leads', methods=['POST'])
def create_lead_endpoint():
    """API endpoint to create a new lead"""
    data = request.json
    email = data.get('email')
    project_description = data.get('project_description')
    status = data.get('status', 'New Lead')
    
    if not email or not project_description:
        return jsonify({"error": "Email and project description are required"}), 400
    
    lead_id = create_lead(email, project_description, status)
    if lead_id:
        return jsonify({
            "success": True,
            "message": "Lead created successfully",
            "lead_id": lead_id
        }), 201
    else:
        return jsonify({"error": "Failed to create lead"}), 500

@app.route('/api/leads/<int:lead_id>')
def get_lead(lead_id):
    """API endpoint to get a specific lead"""
    lead = get_lead_by_id(lead_id)
    if lead:
        return jsonify(lead)
    return jsonify({"error": "Lead not found"}), 404

@app.route('/api/leads/<int:lead_id>', methods=['PUT'])
def update_lead(lead_id):
    """API endpoint to update a lead's status"""
    data = request.json
    status = data.get('status')
    
    if not status:
        return jsonify({"error": "Status is required"}), 400
    
    if update_lead_status(lead_id, status):
        return jsonify({"success": True, "message": "Lead updated successfully"})
    
    return jsonify({"error": "Failed to update lead"}), 500

@app.route('/api/leads/<int:lead_id>', methods=['DELETE'])
def delete_lead_endpoint(lead_id):
    """API endpoint to delete a lead"""
    if delete_lead(lead_id):
        return jsonify({"success": True, "message": "Lead deleted successfully"})
    
    return jsonify({"error": "Failed to delete lead"}), 500

@app.route('/api/statistics')
def get_statistics():
    """API endpoint to get lead statistics"""
    stats = get_lead_statistics()
    
    # Prepare chart data for Chart.js
    status_chart_data = prepare_status_chart_data(stats['status_counts'])
    daily_chart_data = prepare_daily_chart_data(stats['daily_counts'])
    
    return jsonify({
        "statistics": stats,
        "charts": {
            "status_chart_data": status_chart_data,
            "daily_chart_data": daily_chart_data
        }
    })

@app.route('/api/send-email', methods=['POST'])
def generate_email_for_lead():
    """API endpoint to generate an email for a lead"""
    data = request.json
    lead_id = data.get('lead_id')
    context = data.get('context', '')
    
    if not lead_id:
        return jsonify({"error": "Lead ID is required"}), 400
    
    # Get lead details
    lead = get_lead_by_id(lead_id)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    
    # Generate email content only (don't send yet)
    result = generate_email_content_only(lead['email'], lead['project_description'], context)
    
    return jsonify(result)

@app.route('/api/send-email-now', methods=['POST'])
def send_email_to_lead():
    """API endpoint to send a pre-generated email to a lead"""
    data = request.json
    lead_id = data.get('lead_id')
    subject = data.get('subject')
    body = data.get('body')
    
    if not lead_id or not subject or not body:
        return jsonify({"error": "Lead ID, subject, and body are required"}), 400
    
    # Get lead details
    lead = get_lead_by_id(lead_id)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    
    # Send the email
    result = send_email_only(lead['email'], subject, body)
    
    return jsonify(result)

@app.route('/api/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "database": "connected" if os.path.exists(DATABASE_PATH) else "not_found"
    })

# ============ RUN THE APP ============
if __name__ == "__main__":
    print("Starting Email Assistant Dashboard...")
    print(f"Database path: {DATABASE_PATH}")
    print("Dashboard will be available at: http://localhost:5001")
    print("Press Ctrl+C to stop the server")
    print("")
    
    app.run(debug=True, host='0.0.0.0', port=5001)




    