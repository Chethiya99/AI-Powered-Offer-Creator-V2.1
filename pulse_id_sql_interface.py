__import__('pysqlite3')
import sys
import os
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import base64
import csv
import io

sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
import re
import pandas as pd
import streamlit as st
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent
from langchain_groq import ChatGroq
from langchain.agents import AgentType
from langchain_community.llms import Ollama
from crewai import Agent, Task, Crew, Process, LLM
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

# Load configuration from secrets.toml
try:
    # Amazon WorkMail Configuration from secrets
    WORKMAIL_SMTP_SERVER = st.secrets["workmail"]["SMTP_SERVER"]
    WORKMAIL_SMTP_PORT = st.secrets["workmail"]["SMTP_PORT"]
    WORKMAIL_USERNAME = st.secrets["workmail"]["USERNAME"]
    WORKMAIL_PASSWORD = st.secrets["workmail"]["PASSWORD"]
    
    # Groq API Key from secrets
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
except KeyError as e:
    st.error(f"Missing configuration in secrets.toml: {str(e)}")
    st.stop()

# Page Configuration
st.set_page_config(
    page_title="Pulse iD - Database Query & Email Generator",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
if 'db' not in st.session_state:
    st.session_state.db = None
if 'agent_executor' not in st.session_state:
    st.session_state.agent_executor = None
if 'merchant_data' not in st.session_state:
    st.session_state.merchant_data = None
if 'raw_output' not in st.session_state:
    st.session_state.raw_output = ""
if 'extraction_results' not in st.session_state:
    st.session_state.extraction_results = None
if 'email_results' not in st.session_state:
    st.session_state.email_results = None
if 'api_key' not in st.session_state:
    st.session_state.api_key = GROQ_API_KEY  # Use the API key from secrets
if 'interaction_history' not in st.session_state:
    st.session_state.interaction_history = []  # Store all interactions (queries, results, emails)
if 'selected_db' not in st.session_state:
    st.session_state.selected_db = "merchant_data_japan.db"  # Default database
if 'db_initialized' not in st.session_state:
    st.session_state.db_initialized = False  # Track if the database is initialized
if 'selected_template' not in st.session_state:
    st.session_state.selected_template = "email_task_description1.txt"  # Default template
if 'trigger_rerun' not in st.session_state:
    st.session_state.trigger_rerun = False  # Track if a re-run is needed
if 'custom_template_content' not in st.session_state:
    # Initialize with default template content
    default_template_path = "email_descriptions/email_task_description1.txt"
    if os.path.exists(default_template_path):
        with open(default_template_path, 'r') as file:
            st.session_state.custom_template_content = file.read()
    else:
        st.session_state.custom_template_content = """Generate a personalized email for the merchant with the following details:
        
Merchant Name: {merchant_data['name']}
Email: {merchant_data['email']}
Business Type: {merchant_data['business_type']}

The email should be in this exact format:
Merchant Name: [Merchant Name]
To: [Recipient Email]
From: [Your Email]
Subject: [Email Subject]
Body: [Email Body - 300 words professional email]

The email should:
1. Be professional yet friendly
2. Mention potential collaboration opportunities
3. Be around 300 words
4. Include a compelling subject line
5. Format all links as HTML <a> tags (e.g., <a href='https://example.com'>Example</a>)"""
if 'email_dataframe' not in st.session_state:
    st.session_state.email_dataframe = pd.DataFrame(columns=['id', 'Merchant_Name', 'To', 'From', 'Subject', 'Body'])

# Function to encode image to base64
def image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# Function to create email with embedded QR code
def create_email_with_qr(receiver_email, subject, body_text, qr_image_path="qr.png"):
    msg = MIMEMultipart('related')
    msg['From'] = WORKMAIL_USERNAME
    msg['To'] = receiver_email
    msg['Subject'] = subject
    
    # Convert the body text to HTML if it's not already
    if not body_text.startswith('<html>'):
        # Convert plain text links to HTML links
        body_text = re.sub(
            r'(https?://\S+)',
            r'<a href="\1">\1</a>',
            body_text
        )
        # Convert newlines to <br> tags
        body_text = body_text.replace('\n', '<br>')
        
        html = f"""
        <html>
            <body>
                {body_text}
            </body>
        </html>
        """
    else:
        html = body_text
    
    msg.attach(MIMEText(html, 'html'))
    return msg

# Function to read the email task description from a text file
def read_email_task_description(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r') as file:
            content = file.read()
            st.session_state.custom_template_content = content  # Update with file content
            return content
    else:
        raise FileNotFoundError(f"The file {file_path} does not exist.")

# Function to send email using Amazon WorkMail
def send_email_workmail(receiver_email, subject, body_text, qr_image_path="qr.png"):
    try:
        # Create the email with embedded QR code
        msg = create_email_with_qr(receiver_email, subject, body_text, qr_image_path)
        
        # Connect to the Amazon WorkMail SMTP server and send
        with smtplib.SMTP_SSL(WORKMAIL_SMTP_SERVER, WORKMAIL_SMTP_PORT) as server:
            server.login(WORKMAIL_USERNAME, WORKMAIL_PASSWORD)
            server.sendmail(WORKMAIL_USERNAME, receiver_email, msg.as_string())

        return True

    except Exception as e:
        st.error(f"Error sending email: {str(e)}")
        return False

# Function to store sent email data in the database
def store_sent_email(merchant_id, email, sent_time):
    try:
        conn = sqlite3.connect('sent_emails.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sent_emails (
                merchantID TEXT,
                email TEXT,
                sent_time DATETIME
            )
        ''')
        cursor.execute('''
            INSERT INTO sent_emails (merchantID, email, sent_time)
            VALUES (?, ?, ?)
        ''', (merchant_id, email, sent_time))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Error storing email data: {str(e)}")
        return False

# Function to convert plain text links to HTML links
def convert_links_to_html(text):
    # Convert URLs to HTML links
    text = re.sub(
        r'(https?://\S+)',
        r'<a href="\1">\1</a>',
        text
    )
    return text

# Function to parse email components from structured format
def parse_email_components(email_text):
    components = {
        'Merchant_Name': '',
        'To': '',
        'From': '',
        'Subject': '',
        'Body': ''
    }
    
    # Extract Merchant Name
    merchant_match = re.search(r"Merchant Name:\s*(.*?)(?=\nTo:|$)", email_text, re.IGNORECASE)
    if merchant_match:
        components['Merchant_Name'] = merchant_match.group(1).strip()
    
    # Extract To
    to_match = re.search(r"To:\s*(.*?)(?=\nFrom:|$)", email_text, re.IGNORECASE)
    if to_match:
        components['To'] = to_match.group(1).strip()  # Hardcoded recipient
    
    # Extract From
    from_match = re.search(r"From:\s*(.*?)(?=\nSubject:|$)", email_text, re.IGNORECASE)
    if from_match:
        components['From'] = from_match.group(1).strip()
    
    # Extract Subject
    subject_match = re.search(r"Subject:\s*(.*?)(?=\nBody:|$)", email_text, re.IGNORECASE)
    if subject_match:
        components['Subject'] = subject_match.group(1).strip()
    
    # Extract Body
    body_match = re.search(r"Body:\s*(.*)", email_text, re.IGNORECASE | re.DOTALL)
    if body_match:
        body_text = body_match.group(1).strip()
        # Convert links in the body to HTML format
        components['Body'] = convert_links_to_html(body_text)
    
    return components

# Function to update the email dataframe
def update_email_dataframe(email_text, email_id):
    components = parse_email_components(email_text)
    
    new_row = {
        'id': email_id,
        'Merchant_Name': components['Merchant_Name'],
        'To': components['To'],
        'From': components['From'],
        'Subject': components['Subject'],
        'Body': components['Body']
    }
    
    # Convert new_row to a DataFrame and concatenate with the existing one
    new_df = pd.DataFrame([new_row])
    st.session_state.email_dataframe = pd.concat([st.session_state.email_dataframe, new_df], ignore_index=True)

# Header Section with Title and Logo
st.image("logo.png", width=150)  # Ensure you have your logo in the working directory
st.markdown(
    "<h1 style='text-align: center; color: #4CAF50;'>📊 PulseID Merchant Scout Agent</h1>",
    unsafe_allow_html=True
)
st.markdown(
    "<h4 style='text-align: center; color: #555;'>Interact with your merchant database and generate emails with ease!</h4>",
    unsafe_allow_html=True
)

# Sidebar Configuration
st.sidebar.header("Settings")

# QR Code Preview
st.sidebar.markdown("### QR Code Preview")
try:
    qr_base64 = image_to_base64("qr.png")
    st.sidebar.image(f"data:image/png;base64,{qr_base64}", caption="Company QR Code", width=150)
except FileNotFoundError:
    st.sidebar.warning("QR code image (qr.png) not found")

# Database Selection
db_options = ["merchant_data_japan.db"]
new_selected_db = st.sidebar.selectbox("Select Database:", db_options, index=db_options.index(st.session_state.selected_db))

# Check if the database selection has changed
if new_selected_db != st.session_state.selected_db:
    st.session_state.selected_db = new_selected_db
    st.session_state.db_initialized = False  # Reset database initialization
    st.sidebar.success(f"✅ Switched to database: {st.session_state.selected_db}")

# Model Selection
model_name = st.sidebar.selectbox("Select Model:", ["llama3-70b-8192"])

# Email Template Selection
template_options = ["email_task_description1.txt", "email_task_description2.txt", "email_task_description3.txt"]
new_selected_template = st.sidebar.selectbox("Select Email Prompt:", template_options, index=template_options.index(st.session_state.selected_template))

# Check if template selection has changed
if new_selected_template != st.session_state.selected_template:
    st.session_state.selected_template = new_selected_template
    # Read the new template content
    description_file_path = f"email_descriptions/{st.session_state.selected_template}"
    read_email_task_description(description_file_path)
    st.sidebar.success(f"✅ Selected Prompt: {st.session_state.selected_template}")

# Initialize SQL Database and Agent
if st.session_state.selected_db and not st.session_state.db_initialized:
    try:
        # Initialize Groq LLM
        llm = ChatGroq(
            temperature=0,
            model_name=model_name,
            api_key=st.session_state.api_key
        )

        # Initialize SQLDatabase
        st.session_state.db = SQLDatabase.from_uri(f"sqlite:///{st.session_state.selected_db}", sample_rows_in_table_info=3)

        # Create SQL Agent
        st.session_state.agent_executor = create_sql_agent(
            llm=llm,
            db=st.session_state.db,
            agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
            verbose=True
        )
        st.session_state.db_initialized = True  # Mark database as initialized
        st.sidebar.success("✅ Database and LLM Connected Successfully!")
    except Exception as e:
        st.sidebar.error(f"Error: {str(e)}")

# Custom Template Editor at the top of the main content
st.markdown("### Email Prompt Editor", unsafe_allow_html=True)
st.info("Edit the email prompt below. Your changes will be used when generating emails.")

# Text area for editing the template content
custom_template = st.text_area(
    "Edit Email Prompt:",
    value=st.session_state.custom_template_content,
    height=300,
    key="custom_template_editor"
)

# Update button for the template
if st.button("Update Template"):
    st.session_state.custom_template_content = custom_template
    st.success("✅ Template updated successfully!")

# Function to check if extraction results contain merchant data
def has_merchant_data(extraction_results):
    if not extraction_results:
        return False
    
    # Convert to string if it's not already
    results_str = str(extraction_results)
    
    # Check for common patterns that indicate merchant data
    patterns = [
        r'merchant.*name',
        r'email.*@',
        r'business.*type',
        r'cuisine.*type',
        r'review',
        r'rating'
    ]
    
    # If any pattern matches, consider it as having merchant data
    for pattern in patterns:
        if re.search(pattern, results_str, re.IGNORECASE):
            return True
    
    return False

# Function to render the "Enter Query" section
def render_query_section():
    st.markdown("#### Get to know the Merchant Target List:", unsafe_allow_html=True)
    
    # Text area for user input
    user_query = st.text_area("Enter your query:", placeholder="E.g., Give first three merchant names and their emails, ratings, cuisine type and reviews.", key=f"query_{len(st.session_state.interaction_history)}", value=st.session_state.get('user_query', ''))
    
    if st.button("Run Query", key=f"run_query_{len(st.session_state.interaction_history)}"):
        if user_query:
            with st.spinner("Running query..."):
                try:
                    # Define company details and agent role
                    company_details = """
                     If possible, Please always try to give answers in a table format or point wise.
                    """

                    # Prepend company details to the user's query
                    full_query = f"{company_details}\n\nUser Query: {user_query}"

                    # Execute the query using the agent
                    result = st.session_state.agent_executor.invoke(full_query)
                    st.session_state.raw_output = result['output'] if isinstance(result, dict) else result
                    
                    # Process raw output using an extraction agent 
                    extractor_llm = LLM(model="groq/llama-3.3-70b-versatile", api_key=st.session_state.api_key)
                    extractor_agent = Agent(
                        role="Data Extractor",
                        goal="Extract merchants, emails, reviews and anything posible from the raw output if they are only available.",
                        backstory="You are an expert in extracting structured information from text.",
                        provider="Groq",
                        llm=extractor_llm 
                    )
                    
                    extract_task = Task(
                        description=f"Extract a list of 'merchants' and their 'emails', 'reviews' from the following text:\n\n{st.session_state.raw_output}",
                        agent=extractor_agent,
                        expected_output="if available, Please return A structured list of merchant names, their associated email addresses, reviews etc extracted from the given text"
                    )
                    
                    # Crew execution for extraction 
                    extraction_crew = Crew(agents=[extractor_agent], tasks=[extract_task], process=Process.sequential)
                    extraction_results = extraction_crew.kickoff()
                    
                    # Check if we have enough merchant data to proceed
                    if has_merchant_data(extraction_results):
                        st.session_state.extraction_results = extraction_results
                        st.session_state.merchant_data = st.session_state.extraction_results
                        
                        # Append the query and results to the interaction history
                        st.session_state.interaction_history.append({
                            "type": "query",
                            "content": {
                                "query": user_query,
                                "raw_output": st.session_state.raw_output,
                                "extraction_results": st.session_state.extraction_results
                            }
                        })
                        
                        # Trigger a re-run to update the UI
                        st.session_state.trigger_rerun = True
                    else:
                        st.warning("⚠️ No enough merchant data found to proceed. Please refine your query to include merchant names, emails, and other relevant details.")
                except Exception as e:
                    st.error(f"Error executing query: {str(e)}")
        else:
            st.warning("⚠️ Please enter a query before clicking 'Run Query'.")

# Display Interaction History
if st.session_state.interaction_history:
    st.markdown("### Interaction History:", unsafe_allow_html=True)
    for idx, interaction in enumerate(st.session_state.interaction_history):
        if interaction["type"] == "query":
            st.markdown(f"#### Query: {interaction['content']['query']}")
            st.markdown("**Raw Output:**")
            st.write(interaction['content']['raw_output'])
            
            # Only display extracted merchants if there is data and it does not contain ''
            if interaction['content']['extraction_results'] and interaction['content']['extraction_results'].raw and 'errorhappened' not in interaction['content']['extraction_results'].raw:
                
                # Show the "Generate Emails" button for this specific interaction
                if st.button(f"Generate Emails For Above Extracted Merchants", key=f"generate_emails_{idx}"):
                    with st.spinner("Generating emails..."):
                        try:
                            # Define email generation agent 
                            llm_email = LLM(model="groq/llama-3.3-70b-versatile", api_key=st.session_state.api_key)
                            email_agent = Agent(
                                role="Assume yourself as a lead Marketing Lead, with years of experiences working for leading merchant sourcing and acquiring companies such as wirecard, cardlytics, fave that has helped to connect with small to medium merchants to source an offer. Generate a personalized email for merchants with a compelling and curiosity-piquing subject line that feels authentic and human-crafted, ensuring the recipient does not perceive it as spam or automated",
                                goal="Generate personalized marketing emails for merchants.Each email should contains at least 300 words",
                                backstory="You are a marketing expert named 'Rasika Galhena' of Pulse iD fintech company skilled in crafting professional and engaging emails for merchants.",
                                verbose=True,
                                allow_delegation=False,
                                llm=llm_email 
                            )

                            # Use the custom template content from the editor
                            email_task_description = st.session_state.custom_template_content

                            # Email generation task using extracted results 
                            task = Task(
                                description=email_task_description.format(merchant_data=interaction['content']['extraction_results'].raw),
                                agent=email_agent,
                                expected_output="Marketing emails for each selected merchant, tailored to their business details. Each email must be in this exact format:\n\nMerchant Name: [Merchant Name]\nTo: [Recipient Email]\nFrom: [Your Email]\nSubject: [Email Subject]\nBody: [Email Body - 300 words professional email with HTML links]\n\n---\n\n[Next Email]"
                            )

                            # Crew execution 
                            crew = Crew(agents=[email_agent], tasks=[task], process=Process.sequential)
                            email_results = crew.kickoff()
                            
                            # Display results 
                            if email_results.raw:
                                # Split the email results into individual emails (assuming emails are separated by a delimiter like "---")
                                individual_emails = email_results.raw.split("---")
                                
                                # Store each email separately in the interaction history
                                for i, email_text in enumerate(individual_emails):
                                    if email_text.strip():  # Skip empty emails
                                        # Append the generated email to the interaction history
                                        st.session_state.interaction_history.append({
                                            "type": "email",
                                            "content": email_text,
                                            "index": len(st.session_state.interaction_history)  # Unique index for each email
                                        })
                                        
                                        # Update the email dataframe
                                        update_email_dataframe(email_text, len(st.session_state.interaction_history))
                                
                                # Trigger a re-run to update the UI
                                st.session_state.trigger_rerun = True
                            else:
                                st.warning("No emails could be generated from the available data.")

                        except Exception as e:
                            st.error(f"Error generating emails: {str(e)}")
        
        elif interaction["type"] == "email":
            st.markdown("#### Generated Email:")
            
            # Display the structured email content with HTML rendering for the body
            components = parse_email_components(interaction['content'])
            
            st.text(f"Merchant Name: {components['Merchant_Name']}")
            st.text(f"To: {components['To']}")
            st.text(f"From: {components['From']}")
            st.text(f"Subject: {components['Subject']}")
            st.markdown("**Body:**", unsafe_allow_html=True)
            st.markdown(components['Body'], unsafe_allow_html=True)
            
            # Add a "Send" button for each email
            if st.button(f"Send Email {interaction['index']}", key=f"send_email_{interaction['index']}"):
                with st.spinner("Sending email..."):
                    try:
                        # Send the email using Amazon WorkMail
                        if send_email_workmail(components['To'], components['Subject'], components['Body']):
                            # Store the sent email data in the database
                            sent_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            if store_sent_email(components['Merchant_Name'], components['To'], sent_time):
                                st.success(f"✅ Email sent to {components['To']} and stored in the database.")
                            else:
                                st.error("Failed to store email data in the database.")
                        else:
                            st.error("Failed to send email.")
                    except Exception as e:
                        st.error(f"Error sending email: {str(e)}")
        
        st.markdown("---")

# CSV Download Section
if not st.session_state.email_dataframe.empty:
    st.markdown("### Generated Emails CSV Export")
    
    # Display the dataframe with HTML rendering for the body
    display_df = st.session_state.email_dataframe.copy()
    display_df['Body'] = display_df['Body'].apply(lambda x: x.replace('\n', '<br>') if isinstance(x, str) else x)
    st.write(display_df.to_html(escape=False, index=False), unsafe_allow_html=True)
    
    # Create a download link for the CSV
    def convert_df_to_csv(df):
        output = io.StringIO()
        # Ensure HTML tags are preserved in the CSV
        df.to_csv(output, index=False, quoting=csv.QUOTE_NONNUMERIC, escapechar='\\')
        return output.getvalue()
    
    csv = convert_df_to_csv(st.session_state.email_dataframe)
    st.download_button(
        label="Download CSV",
        data=csv,
        file_name="generated_emails.csv",
        mime="text/csv"
    )

# Always render the "Ask questions about your database" section
render_query_section()

# Trigger a re-run if needed
if st.session_state.trigger_rerun:
    st.session_state.trigger_rerun = False  # Reset the trigger
    st.rerun()  # Force a re-run of the script

# Footer Section 
st.markdown("---")
st.markdown(
    "<div style='text-align: center; font-size: 14px;'>Powered by <strong>Pulse iD</strong> | Built with 🐍 Python and Streamlit</div>",
    unsafe_allow_html=True 
)
