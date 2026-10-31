"""From the repository root: python -m streamlit run streamlit-ui/streamlit_app.py."""

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from api_client import AssistantError, ask_question
from app_config import APP_DESCRIPTION, APP_TITLE, MAX_QUESTION_LENGTH, load_settings


# Global UI defaults and session keys.
QUESTION_PLACEHOLDER = "For example: Do pending practice hours count toward completion?"
RESULT_KEY = "assistant_result"
QUESTION_KEY = "answered_question"
ERROR_KEY = "assistant_error"


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="📚", layout="centered")
    st.title(APP_TITLE)
    st.write(APP_DESCRIPTION)

    try:
        secrets = dict(st.secrets)
    except (FileNotFoundError, StreamlitSecretNotFoundError):
        secrets = {}
    try:
        settings = load_settings(secrets)
    except ValueError:
        st.error("The assistant needs a configuration update. Please contact the administrator.")
        return

    if not settings.api_url:
        st.info("The assistant is being set up. Answers will be available once it is connected.")

    with st.form("question_form"):
        question = st.text_area(
            "Your question",
            placeholder=QUESTION_PLACEHOLDER,
            max_chars=MAX_QUESTION_LENGTH,
            height=110,
        )
        submitted = st.form_submit_button("Ask", type="primary", disabled=not settings.api_url)

    if submitted:
        st.session_state.pop(RESULT_KEY, None)
        st.session_state.pop(QUESTION_KEY, None)
        st.session_state.pop(ERROR_KEY, None)
        try:
            with st.spinner("Finding an answer…"):
                st.session_state[RESULT_KEY] = ask_question(question, settings)
                st.session_state[QUESTION_KEY] = question.strip()
        except AssistantError as error:
            st.session_state[ERROR_KEY] = str(error)

    if st.session_state.get(ERROR_KEY):
        st.error(st.session_state[ERROR_KEY])
    result = st.session_state.get(RESULT_KEY)
    if result:
        st.subheader("Answer")
        st.caption("Question answered:")
        st.text(st.session_state[QUESTION_KEY])
        # Plain text prevents model-generated links from bypassing the sources list.
        st.text(result.text)
        st.subheader("Sources")
        if not result.sources:
            st.warning("No source references were returned for this answer.")
        for source in result.sources:
            st.text(source.title)
            if source.url:
                st.link_button("Open source", source.url)
            else:
                if source.uri:
                    st.text(source.uri)
                st.caption("A document link is not available for this source.")


if __name__ == "__main__":
    main()
