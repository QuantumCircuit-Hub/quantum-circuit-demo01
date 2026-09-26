"""Sidebar navigation for the QCH Streamlit demo (presentation only).

Streamlit's automatic page list is turned off (.streamlit/config.toml:
client.showSidebarNavigation = false) so the public sidebar shows exactly the
two main pages below. pages/circuit_detail.py stays a normal, routable page
(the evolution-graph version boxes open /circuit_detail?circuit=...&version=...
in a new tab); it is only left out of the list.
"""

from __future__ import annotations

import streamlit as st

MAIN_PAGE_TITLE = "Multi-Version Circuit Explorer"
ASK_PAGE_TITLE = "Ask QCH with Natural Language"


def render_main_navigation() -> None:
    with st.sidebar:
        st.page_link("app.py", label=MAIN_PAGE_TITLE, icon=":material/account_tree:")
        st.page_link("pages/Ask_QCH.py", label=ASK_PAGE_TITLE, icon=":material/chat:")
        st.divider()
