"""From the repository root: python -m streamlit run streamlit-ui/streamlit_app.py."""

from html import escape

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from api_client import AssistantError, ask_question
from app_config import (
    APP_TITLE, ASSETS_DIRECTORY, DEMO_DOCUMENT_DIRECTORY, DOCUMENT_CATALOG,
    MAX_QUESTION_LENGTH, SUGGESTED_QUESTIONS, load_settings,
)


# Global UI copy and session keys.
QUESTION_PLACEHOLDER = "What would you like to understand?"
RESULT_KEY = "assistant_result"
QUESTION_KEY = "answered_question"
INPUT_KEY = "question_input"
ERROR_KEY = "assistant_error"
NAVIGATION_OPTIONS = ("Ask", "Documents")


def choose_question(question):
    """Copy a suggested question into the input box without submitting it automatically."""
    st.session_state[INPUT_KEY] = question


@st.dialog("Learning document", width="large")
def show_document(index):
    """Open a local sample document so a learner can read the original material."""
    filename, title, _ = DOCUMENT_CATALOG[index]
    st.subheader(title)
    st.caption("Fictional sample content · learning program demo")
    try:
        content = (DEMO_DOCUMENT_DIRECTORY / filename).read_text(encoding="utf-8")
    except OSError:
        st.error("This document is not available right now.")
        return
    st.text(content)


def render_library(result=None, *, expanded=False):
    """List the sample documents and mark any that support the current answer."""
    with st.container(key="document_panel"):
        st.html(f'<div class="panel-heading"><h2>Learning documents</h2>'
                f'<span class="count-badge">{len(DOCUMENT_CATALOG)} documents</span></div>')
        st.caption("Sample learning library")
        for index, (filename, title, description) in enumerate(DOCUMENT_CATALOG):
            cited = result and any(source.uri.endswith("/" + filename) for source in result.sources)
            with st.container(key=f"document_{index}"):
                st.html(f'<div class="document-title">▤ &nbsp; {escape(title)}</div>'
                        f'<div class="document-description">{escape(description)}</div>'
                        + ('<span class="document-used">Referenced in this answer</span>' if cited else ''))
                if expanded:
                    st.caption("Fictional program guidance · text document")
                if st.button("Read document ↗", key=f"read_document_{index}"):
                    show_document(index)


def render_suggestions():
    """Show example questions to help learners understand what they can ask."""
    with st.container(key="question_suggestions"):
        st.html('<p class="suggestions-heading">☼ &nbsp; Try asking</p>')
        for index, question in enumerate(SUGGESTED_QUESTIONS):
            st.button(question + "  →", key=f"suggestion_{index}", width="stretch",
                      on_click=choose_question, args=(question,))


def render_answer():
    """Display the latest answer and source links, or explain an error or empty result."""
    if st.session_state.get(ERROR_KEY):
        st.error(st.session_state[ERROR_KEY])
    result = st.session_state.get(RESULT_KEY)
    if not result:
        with st.container(key="empty_answer"):
            st.html('<div class="empty-icon">▤</div>'
                    '<p class="empty-title">A little clarity, backed by your documents.</p>'
                    '<p class="empty-copy">Ask a question above. Your answer and supporting '
                    'sources will appear here, so you can explore the details for yourself.</p>')
        return
    with st.container(key="answer_card"):
        count_label = "source" if len(result.sources) == 1 else "sources"
        st.html('<div class="answer-heading"><h2>Here’s what your documents say</h2>'
                f'<span class="source-badge">{len(result.sources)} {count_label}</span></div>')
        st.caption("Question answered: " + st.session_state[QUESTION_KEY])
        st.text(result.text)
        st.html('<div class="source-label">↗ &nbsp; SUPPORTING SOURCES</div>')
        if not result.sources:
            st.warning("No source references were returned for this answer.")
        for index in range(0, len(result.sources), 2):
            for offset, (column, source) in enumerate(zip(st.columns(2), result.sources[index:index + 2])):
                with column, st.container(key=f"source_{index + offset}"):
                    st.text(source.title)
                    if source.url:
                        st.link_button("Open source ↗", source.url)
                    else:
                        if source.uri:
                            st.text(source.uri)
                        st.caption("A document link is not available for this source.")


def main():
    """Build the learning interface, load its connection settings, and handle question submissions."""
    # Apply the shared page identity and theme before showing the learning workspace.
    st.set_page_config(page_title=APP_TITLE, page_icon="📖", layout="wide")
    st.html(f'<style>{(ASSETS_DIRECTORY / "styles.css").read_text(encoding="utf-8")}</style>')
    # Let learners switch between asking a question and browsing original documents.
    with st.sidebar:
        st.html('<div class="brand-mark">♧</div><div class="brand-name">Learning<br>Assistant</div>')
        page = st.radio("Navigation", NAVIGATION_OPTIONS, label_visibility="collapsed")
        st.html('<div class="sidebar-note"><p>Knowledge<br>within reach.</p><div class="gold-line"></div></div>')

    if page == "Documents":
        st.html('<div class="hero"><p class="eyebrow">YOUR LEARNING SPACE</p>'
                '<h1>A place for every answer to begin.</h1>'
                '<p>Explore the sample guides behind your learning program.</p></div>')
        render_library(expanded=True)
        return

    # Load private connection settings from Streamlit; the interface can still show documents before setup.
    try:
        secrets = dict(st.secrets)
    except (FileNotFoundError, StreamlitSecretNotFoundError):
        secrets = {}
    try:
        settings = load_settings(secrets)
    except ValueError:
        st.error("The assistant needs a configuration update. Please contact the administrator.")
        return

    # Place the question and answer beside the source library so learners can check the evidence.
    main_column, library_column = st.columns([2.5, 1], gap="large")
    with main_column:
        book = (ASSETS_DIRECTORY / "book.svg").read_text(encoding="utf-8")
        st.html('<div class="hero"><p class="eyebrow">YOUR LEARNING SPACE</p>'
                '<h1>Find clarity in every question.</h1>'
                '<p>Explore your learning documents with answers you can trace.</p>'
                f'<div class="hero-art">{book}</div></div>')
        if not settings.api_url:
            st.info("The assistant is being set up. You can explore the documents while we get connected.")
        # Submit only when the learner chooses Find answer, avoiding a request for every keystroke.
        with st.form("question_form"):
            st.html('<div class="composer-title">Ask your learning assistant</div>')
            question = st.text_area("Your question", placeholder=QUESTION_PLACEHOLDER,
                                    key=INPUT_KEY, max_chars=MAX_QUESTION_LENGTH,
                                    height=100, label_visibility="collapsed")
            hint, action = st.columns([2, 1])
            with hint:
                st.html('<div class="composer-hint">Search across your learning documents</div>')
            with action:
                submitted = st.form_submit_button("Find answer ↗", type="primary",
                                                  width="stretch", disabled=not settings.api_url)
        # Clear the previous result and show progress, so a failed question never leaves an old answer visible.
        if submitted:
            for key in (RESULT_KEY, QUESTION_KEY, ERROR_KEY):
                st.session_state.pop(key, None)
            try:
                with st.spinner("Finding an answer…"):
                    st.session_state[RESULT_KEY] = ask_question(question, settings)
                    st.session_state[QUESTION_KEY] = question.strip()
            except AssistantError as error:
                st.session_state[ERROR_KEY] = str(error)
        render_answer()
        st.html('<div class="footer-note">Grounded in your documents. Check the sources to explore further.</div>')
    with library_column:
        render_library(st.session_state.get(RESULT_KEY))
        render_suggestions()


if __name__ == "__main__":
    main()