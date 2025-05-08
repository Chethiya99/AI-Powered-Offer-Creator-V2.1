import streamlit as st
import openai
import json
import re
from datetime import datetime, timedelta
import requests
from typing import List, Dict

# --- Initialize Session State ---
if 'offer_params' not in st.session_state:
    st.session_state.offer_params = None
if 'offer_created' not in st.session_state:
    st.session_state.offer_created = False
if 'adjusted_params' not in st.session_state:
    st.session_state.adjusted_params = None
if 'lms_credentials' not in st.session_state:
    st.session_state.lms_credentials = {
        'email': st.secrets.get("LMS_EMAIL", ""),
        'password': st.secrets.get("LMS_PASSWORD", ""),
        'app': 'lms'
    }
if 'pending_offers' not in st.session_state:
    st.session_state.pending_offers = None
if 'filtered_offers' not in st.session_state:
    st.session_state.filtered_offers = None

# --- Helper Functions ---
def format_currency(amount):
    return f"${amount:.2f}"

def authenticate_user(email: str, password: str, app: str):
    url = 'https://lmsdev.pulseid.com/1.0/auth/login-v2'
    headers = {'Content-Type': 'application/json'}
    payload = {'email': email, 'password': password, 'app': app}
    response = requests.post(url, headers=headers, json=payload)
    if not response.ok:
        raise Exception('Authentication failed')
    auth_data = response.json()
    return {
        'permissionToken': auth_data['data']['auth'][0]['permissionToken'],
        'authToken': auth_data['data']['auth'][0]['authToken']
    }

def get_pending_offers(permission_token: str, auth_token: str):
    url = 'https://lmsdev-marketplace-api.pulseid.com/offer/pending-review'
    headers = {
        'x-pulse-current-client': '315',
        'x-pulse-token': permission_token,
        'Authorization': f'Bearer {auth_token}',
        'Content-Type': 'application/json'
    }
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception('Failed to retrieve offers')
    return response.json()

def fetch_pending_offers():
    try:
        auth = authenticate_user(
            email=st.session_state.lms_credentials['email'],
            password=st.session_state.lms_credentials['password'],
            app=st.session_state.lms_credentials['app']
        )
        offers = get_pending_offers(auth['permissionToken'], auth['authToken'])
        st.session_state.pending_offers = offers.get('offers', [])
        return offers
    except Exception as e:
        st.error(f"Failed to fetch offers: {str(e)}")
        return None

def filter_offers_with_llm(prompt: str, offers: List[Dict]) -> List[Dict]:
    """Use LLM to filter offers based on natural language prompt"""
    try:
        client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
        
        # Prepare offers data for LLM
        offers_str = "\n".join([
            f"ID: {offer.get('id')}, "
            f"Title: {offer.get('title')}, "
            f"Merchant: {offer.get('merchants', [{}])[0].get('name', 'N/A')}, "
            f"Category: {offer.get('merchants', [{}])[0].get('category', 'N/A')}, "
            f"Expires: {offer.get('duration', {}).get('to', 'N/A')}, "
            f"Budget: {offer.get('budget', 'N/A')}"
            for offer in offers
        ])
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": f"""Analyze these offers and return ONLY a JSON list of offer IDs that match this query: '{prompt}'.
                    Available offers:\n{offers_str}\n
                    Return format: {{"matching_ids": [id1, id2, ...]}}"""
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        
        result = json.loads(response.choices[0].message.content)
        return [offer for offer in offers if offer.get('id') in result.get('matching_ids', [])]
    except Exception as e:
        st.error(f"LLM filtering error: {str(e)}")
        return offers

# --- UI Components ---
def offer_card(offer: Dict):
    merchant = offer.get('merchants', [{}])[0]
    image_url = (
        offer.get('offerLogo') or 
        merchant.get('profilePicture') or 
        merchant.get('categoryLogo') or 
        "https://via.placeholder.com/150?text=No+Image"
    )
    
    # Calculate days until expiration
    expiry_date = offer.get('duration', {}).get('to')
    days_left = "N/A"
    if expiry_date and expiry_date != "No end date":
        expiry = datetime.strptime(expiry_date, "%Y-%m-%d %H:%M")
        days_left = max(0, (expiry - datetime.now()).days)
    
    with st.container():
        cols = st.columns([1, 3])
        with cols[0]:
            st.image(image_url, use_column_width=True)
        with cols[1]:
            st.subheader(offer.get('title', 'Untitled Offer'))
            st.markdown(f"""
            **Merchant:** {merchant.get('name', 'N/A')}  
            **Category:** {merchant.get('category', 'N/A')}  
            **Value:** {offer.get('currency', {}).get('symbol', '$')}{offer.get('budget', 'N/A')}  
            **Expires in:** {days_left} days  
            **Status:** {offer.get('status', 'N/A').replace('-', ' ').title()}
            """)
            
            if st.button("View Details", key=f"details_{offer.get('id')}"):
                st.json(offer)
        st.divider()

# --- Main UI ---
st.set_page_config(page_title="Offer Management Dashboard", page_icon="🎯", layout="wide")

# Get API keys
if not st.secrets.get("OPENAI_API_KEY"):
    st.error("OpenAI API key not configured")
    st.stop()

# Custom CSS for tabs
st.markdown("""
<style>
    .stTabs [data-baseweb="tab-list"] {
        justify-content: center;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 1rem 2rem;
        font-size: 1.1rem;
        font-weight: 600;
    }
    .offer-card {
        border-radius: 10px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        padding: 1.5rem;
        margin-bottom: 1.5rem;
    }
</style>
""", unsafe_allow_html=True)

# Main tabs
tab1, tab2 = st.tabs(["✨ Create Offer", "📋 View Offers"])

with tab1:
    st.title("AI-Powered Offer Creation")
    col1, col2 = st.columns([1, 1], gap="large")
    
    with col1:
        user_prompt = st.text_area(
            "Describe your offer:",
            height=150,
            placeholder="E.g., 'Give $20 cashback for first 10 customers spending $500+ valid for 7 days'",
            help="The AI will extract parameters from your description"
        )
        
        if st.button("Generate Offer", type="primary"):
            with st.spinner("Creating your offer..."):
                try:
                    client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
                    response = client.chat.completions.create(
                        model="gpt-4",
                        messages=[
                            {
                                "role": "system",
                                "content": """Extract offer details from user description. Return JSON with:
                                {
                                    "offer_type": "cashback/discount/free_shipping",
                                    "value": 20,
                                    "min_spend": 500,
                                    "duration_days": 7,
                                    "offer_name": "Creative Name",
                                    "max_redemptions": 10,
                                    "description": "Marketing text"
                                }"""
                            },
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.2,
                    )
                    
                    content = response.choices[0].message.content
                    content = re.sub(r'```json\n?(.*?)\n?```', r'\1', content, flags=re.DOTALL)
                    st.session_state.offer_params = json.loads(content)
                    st.session_state.adjusted_params = st.session_state.offer_params.copy()
                    st.session_state.offer_created = True
                    st.rerun()
                except Exception as e:
                    st.error(f"Error generating offer: {str(e)}")
    
    with col2:
        if st.session_state.offer_created and st.session_state.adjusted_params:
            params = st.session_state.adjusted_params
            st.subheader("🎯 Offer Preview")
            
            end_date = datetime.now() + timedelta(days=params.get("duration_days", 7))
            value_display = f"{params['value']}%" if params.get("value_type") == "percentage" else format_currency(params['value'])
            
            with st.container():
                st.markdown(f"""
                **✨ {params.get('offer_name', 'Special Offer')}**  
                💵 **{value_display}** {params.get('offer_type')}  
                🛒 Min. spend: **{format_currency(params.get('min_spend', 0))}**  
                ⏳ Valid until: **{end_date.strftime('%b %d, %Y')}**  
                👥 Max redemptions: **{params.get('max_redemptions', 'Unlimited')}**
                """)
                
                if st.button("Publish Offer", type="primary"):
                    with st.spinner("Publishing..."):
                        # Add your publish logic here
                        st.success("Offer published successfully!")

with tab2:
    st.title("Smart Offer Explorer")
    st.caption("View, search, and analyze your offers using natural language")
    
    # Two-column layout
    col1, col2 = st.columns([1, 3], gap="large")
    
    with col1:
        st.subheader("🔍 Smart Search")
        search_query = st.text_input(
            "Ask about offers:",
            placeholder="E.g., 'Show food offers expiring soon', 'Find high-value cashback deals'",
            help="The AI will analyze your offers and find matches"
        )
        
        if st.button("Search Offers", type="primary"):
            if st.session_state.pending_offers and search_query:
                with st.spinner("Analyzing offers..."):
                    st.session_state.filtered_offers = filter_offers_with_llm(
                        search_query, 
                        st.session_state.pending_offers
                    )
            elif not st.session_state.pending_offers:
                st.warning("No offers loaded. Refresh first.")
        
        st.divider()
        
        if st.button("🔄 Refresh All Offers", type="secondary"):
            with st.spinner("Loading offers..."):
                fetch_pending_offers()
                st.session_state.filtered_offers = None
                st.rerun()
        
        st.markdown("**Quick Filters:**")
        if st.button("Expiring Soon"):
            st.session_state.filtered_offers = [
                o for o in st.session_state.pending_offers 
                if o.get('duration', {}).get('to') and 
                (datetime.strptime(o['duration']['to'], "%Y-%m-%d %H:%M") - datetime.now()).days <= 7
            ]
        
        if st.button("High Value (>$50)"):
            st.session_state.filtered_offers = [
                o for o in st.session_state.pending_offers 
                if float(o.get('budget', 0)) > 50
            ]
    
    with col2:
        offers_to_display = st.session_state.filtered_offers or st.session_state.pending_offers
        
        if offers_to_display:
            st.subheader(f"📋 Offers ({len(offers_to_display)})")
            
            # Sort by expiration date
            offers_to_display.sort(key=lambda x: (
                datetime.strptime(x.get('duration', {}).get('to', '9999-12-31')), 
                "%Y-%m-%d %H:%M"
            ) if x.get('duration', {}).get('to') else '9999-12-31')
            
            for offer in offers_to_display:
                offer_card(offer)
        else:
            st.info("No offers found. Refresh offers or try a different search.")
