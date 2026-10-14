import sys
import importlib
from pathlib import Path

import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from frontend.services import api_client

# Streamlit can retain an already-imported service module after code changes.
# Reload it so newly added authentication helpers are always available.
api_client = importlib.reload(api_client)

st.set_page_config(page_title='ATS Resume Scorer', page_icon='🎯', layout='wide', initial_sidebar_state='expanded')

for key, default in [('access_token', None), ('user_id', None), ('user_email', None), ('current_view', 'landing')]:
    if key not in st.session_state:
        st.session_state[key] = default


def load_css() -> str:
    try:
        return f"<style>{(Path(__file__).parent / 'assets' / 'styles.css').read_text()}</style>"
    except FileNotFoundError:
        return ''


def _set_session(session: dict) -> None:
    st.session_state.access_token = session['access_token']
    st.session_state.user_id = session['user_id']
    st.session_state.user_email = session['email']


def _error_message(exc: requests.RequestException, fallback: str) -> str:
    if getattr(exc, 'response', None) is None:
        return 'Could not reach the backend. Please start the backend on port 8000.'
    try:
        return exc.response.json().get('detail', fallback)
    except ValueError:
        return fallback


def _show_auth() -> None:
    if st.session_state.access_token:
        st.success(f"Signed in as {st.session_state.user_email}")
        if st.button('Sign out', use_container_width=True):
            for key in ('access_token', 'user_id', 'user_email', 'scorer_analysis', 'scorer_pdf_bytes'):
                st.session_state.pop(key, None)
            st.rerun()
        return

    sign_in_tab, sign_up_tab = st.tabs(['Sign in', 'Create account'])
    with sign_in_tab:
        with st.form('sign_in_form'):
            email = st.text_input('Email', key='sign_in_email')
            password = st.text_input('Password', type='password', key='sign_in_password')
            submitted = st.form_submit_button('Sign in', use_container_width=True)
        if submitted:
            try:
                _set_session(api_client.sign_in(email, password))
                st.rerun()
            except requests.RequestException as exc:
                st.error(_error_message(exc, 'Sign in failed.'))
    with sign_up_tab:
        with st.form('sign_up_form'):
            email = st.text_input('Email', key='sign_up_email')
            password = st.text_input('Password (at least 6 characters)', type='password', key='sign_up_password')
            submitted = st.form_submit_button('Create account', use_container_width=True)
        if submitted:
            try:
                _set_session(api_client.sign_up(email, password))
                st.rerun()
            except requests.RequestException as exc:
                st.error(_error_message(exc, 'Could not create account.'))


st.markdown(load_css(), unsafe_allow_html=True)

with st.sidebar:
    st.markdown('## Navigation')
    for label, view in [('🏠 Home', 'landing'), ('🎯 ATS Scorer', 'scorer'), ('📊 History', 'history'), ('📚 Resources', 'resources')]:
        if st.button(label, use_container_width=True):
            st.session_state.current_view = view
            st.rerun()
    st.markdown('---')
    st.markdown('### Account')
    _show_auth()

if st.session_state.current_view == 'landing':
    from frontend.views import landing
    landing.render()
elif st.session_state.current_view == 'scorer':
    from frontend.views import scorer
    scorer.render()
elif st.session_state.current_view == 'history':
    from frontend.views import history
    history.render()
elif st.session_state.current_view == 'resources':
    from frontend.views import resources
    resources.render()
