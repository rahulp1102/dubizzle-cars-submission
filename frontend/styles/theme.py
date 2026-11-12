"""Dubizzle-inspired light and dark design-system tokens, responsive layout rules, and component CSS."""

from __future__ import annotations

LIGHT_TOKENS: dict[str, str] = {
    "bg": "#F6F7F9",
    "surface": "#FFFFFF",
    "surface_elevated": "#FFFFFF",
    "surface_alt": "#EEF1F5",
    "sidebar_bg": "#FFFFFF",
    "border": "#E2E6EE",
    "border_strong": "#C8D0DC",
    "text": "#14171E",
    "text_secondary": "#525A69",
    "text_muted": "#667085",
    "accent": "#C81E20",
    "accent_hover": "#A61618",
    "accent_soft": "rgba(200, 30, 32, 0.08)",
    "accent_ink": "#FFFFFF",
    "disabled_bg": "#E5E8EE",
    "disabled_text": "#5E6778",
    "success": "#137333",
    "success_soft": "rgba(19, 115, 51, 0.10)",
    "warning": "#A14A07",
    "warning_soft": "rgba(180, 83, 9, 0.11)",
    "info": "#1D4ED8",
    "info_soft": "rgba(29, 78, 216, 0.09)",
    "danger": "#B42318",
    "danger_soft": "rgba(180, 35, 24, 0.10)",
    "shadow_sm": "0 1px 2px rgba(16, 24, 40, 0.04)",
    "shadow": "0 1px 3px rgba(16, 24, 40, 0.06), 0 1px 2px rgba(16, 24, 40, 0.04)",
    "shadow_lg": "0 12px 32px rgba(16, 24, 40, 0.14)",
    "chat_user_bg": "#EEF1F6",
    "chat_assistant_bg": "#FFFFFF",
    "placeholder_bg": "#EEF1F5",
    "placeholder_ink": "#64748B",
}

DARK_TOKENS: dict[str, str] = {
    "bg": "#111317",
    "surface": "#181B22",
    "surface_elevated": "#20242E",
    "surface_alt": "#222733",
    "sidebar_bg": "#14171D",
    "border": "#2A2F3B",
    "border_strong": "#3C4354",
    "text": "#F3F4F6",
    "text_secondary": "#A1AAB8",
    "text_muted": "#8A94A6",
    "accent": "#E23E40",
    "accent_hover": "#C81E20",
    "accent_soft": "rgba(226, 62, 64, 0.16)",
    "accent_ink": "#FFFFFF",
    "disabled_bg": "#252A36",
    "disabled_text": "#8B95A7",
    "success": "#34D399",
    "success_soft": "rgba(52, 211, 153, 0.14)",
    "warning": "#FBBF24",
    "warning_soft": "rgba(251, 191, 36, 0.15)",
    "info": "#60A5FA",
    "info_soft": "rgba(96, 165, 250, 0.15)",
    "danger": "#F87171",
    "danger_soft": "rgba(248, 113, 113, 0.15)",
    "shadow_sm": "0 1px 3px rgba(0, 0, 0, 0.28)",
    "shadow": "0 2px 8px rgba(0, 0, 0, 0.36)",
    "shadow_lg": "0 16px 36px rgba(0, 0, 0, 0.55)",
    "chat_user_bg": "#212632",
    "chat_assistant_bg": "#181B22",
    "placeholder_bg": "#1D212A",
    "placeholder_ink": "#9CA3AF",
}


def get_theme_tokens(theme: str) -> dict[str, str]:
    """Return CSS design tokens for 'light' or 'dark' mode."""
    return DARK_TOKENS if theme == "dark" else LIGHT_TOKENS


def build_theme_css(theme: str) -> str:
    """Return complete design-system CSS tokens, Streamlit overrides, and responsive layout rules."""
    t = get_theme_tokens(theme)
    scheme = "dark" if theme == "dark" else "light"
    return f"""
<style>
:root {{
    --dbz-bg: {t["bg"]};
    --dbz-surface: {t["surface"]};
    --dbz-surface-elevated: {t["surface_elevated"]};
    --dbz-surface-alt: {t["surface_alt"]};
    --dbz-sidebar-bg: {t["sidebar_bg"]};
    --dbz-border: {t["border"]};
    --dbz-border-strong: {t["border_strong"]};
    --dbz-text: {t["text"]};
    --dbz-text-sec: {t["text_secondary"]};
    --dbz-text-muted: {t["text_muted"]};
    --dbz-accent: {t["accent"]};
    --dbz-accent-hover: {t["accent_hover"]};
    --dbz-accent-soft: {t["accent_soft"]};
    --dbz-accent-ink: {t["accent_ink"]};
    --dbz-disabled-bg: {t["disabled_bg"]};
    --dbz-disabled-text: {t["disabled_text"]};
    --dbz-success: {t["success"]};
    --dbz-success-soft: {t["success_soft"]};
    --dbz-warning: {t["warning"]};
    --dbz-warning-soft: {t["warning_soft"]};
    --dbz-info: {t["info"]};
    --dbz-info-soft: {t["info_soft"]};
    --dbz-danger: {t["danger"]};
    --dbz-danger-soft: {t["danger_soft"]};
    --dbz-shadow-sm: {t["shadow_sm"]};
    --dbz-shadow: {t["shadow"]};
    --dbz-shadow-lg: {t["shadow_lg"]};
    --dbz-chat-user: {t["chat_user_bg"]};
    --dbz-chat-assistant: {t["chat_assistant_bg"]};
    --dbz-placeholder-bg: {t["placeholder_bg"]};
    --dbz-placeholder-ink: {t["placeholder_ink"]};
    color-scheme: {scheme};
}}

/* ── Global Canvas & Typography ── */
html, body, [data-testid="stAppViewContainer"], .stApp {{
    background-color: var(--dbz-bg) !important;
    color: var(--dbz-text) !important;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
    overflow-x: hidden !important;
}}

.stApp p, .stApp label, .stApp li, .stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stApp caption, [data-testid="stCaptionContainer"] p {{
    color: var(--dbz-text);
}}

[data-testid="stCaptionContainer"] p {{
    color: var(--dbz-text-sec) !important;
    font-size: 0.8125rem !important;
}}

.stMarkdown code, code {{
    background-color: var(--dbz-surface-alt) !important;
    color: var(--dbz-text) !important;
    border: 1px solid var(--dbz-border) !important;
    padding: 0.1rem 0.35rem !important;
    border-radius: 5px !important;
    font-size: 0.82em !important;
}}

/* ── Hide Noisy Streamlit Chrome & Anchor Icons ── */
footer,
[data-testid="stDecoration"],
[data-testid="stAppDeployButton"],
[data-testid="stHeaderActionElements"] {{
    display: none !important;
}}

[data-testid="stHeader"] {{
    background-color: transparent !important;
}}

@media (max-width: 768px) {{
    [data-testid="stHeader"] {{
        background-color: var(--dbz-bg) !important;
        border-bottom: 1px solid var(--dbz-border) !important;
    }}
}}

[data-testid="stHeader"] button,
[data-testid="collapsedControl"] button,
[data-testid="stExpandSidebarButton"] button {{
    color: var(--dbz-text) !important;
    background-color: var(--dbz-surface) !important;
    border: 1px solid var(--dbz-border-strong) !important;
    border-radius: 8px !important;
}}

/* ── Main Container Geometry (Streamlit 1.67 stMainBlockContainer) ── */
[data-testid="stMainBlockContainer"],
.main .block-container {{
    max-width: 1120px !important;
    margin: 0 auto !important;
    padding-top: 1.15rem !important;
    padding-bottom: 5.5rem !important;
    padding-left: 2rem !important;
    padding-right: 2rem !important;
}}

@media (max-width: 1080px) {{
    [data-testid="stMainBlockContainer"],
    .main .block-container {{
        padding-left: 1.25rem !important;
        padding-right: 1.25rem !important;
    }}
}}

@media (max-width: 768px) {{
    [data-testid="stMainBlockContainer"],
    .main .block-container {{
        padding-top: 3.1rem !important;
        padding-left: 0.875rem !important;
        padding-right: 0.875rem !important;
    }}
}}

/* ── Sidebar Buyer Workspace ── */
[data-testid="stSidebar"] {{
    background-color: var(--dbz-sidebar-bg) !important;
    border-right: 1px solid var(--dbz-border) !important;
}}

[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{
    padding-top: 1rem !important;
    padding-bottom: 2rem !important;
}}

[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] small,
[data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] .stMarkdown div,
[data-testid="stSidebar"] .stMarkdown strong {{
    color: var(--dbz-text) !important;
}}

[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{
    color: var(--dbz-text-sec) !important;
}}

/* ── Native Controls, Selectboxes, DatePicker & Popovers (Full Light + Dark Parity) ── */
input, textarea, select,
[data-baseweb="input"],
[data-baseweb="base-input"],
[data-baseweb="input"] > div,
[data-baseweb="select"] > div,
[data-baseweb="select"] > div > div,
[data-baseweb="select"] div,
[data-baseweb="textarea"],
[data-baseweb="textarea"] > div,
[data-baseweb="datepicker"],
[data-baseweb="datepicker"] > div,
[data-baseweb="datepicker"] input,
[data-testid="stDateInput"] > div,
[data-testid="stDateInputField"],
[data-testid="stDateInputField"] > div,
[data-testid="stDateInput"] .react-aria-DateField,
[data-testid="stDateInput"] input,
[data-testid="stNumberInputContainer"] {{
    background-color: var(--dbz-surface) !important;
    color: var(--dbz-text) !important;
    border-color: var(--dbz-border-strong) !important;
    border-radius: 8px !important;
}}

[data-baseweb="select"] span,
[data-baseweb="select"] input,
[data-baseweb="datepicker"] input,
[data-testid="stDateInputField"] span,
[data-testid="stDateInputField"] [role="spinbutton"],
[data-testid="stDateInput"] .react-aria-DateField span {{
    color: var(--dbz-text) !important;
}}

[data-testid="stDateInputField"] [data-testid="stDateInputCalendarButton"],
[data-testid="stDateInputCalendarButton"] {{
    background-color: transparent !important;
    color: var(--dbz-text-sec) !important;
}}

[data-testid="stDateInputCalendarButton"] svg,
[data-testid="stDateInput"] svg {{
    fill: var(--dbz-text-sec) !important;
    color: var(--dbz-text-sec) !important;
}}

input::placeholder, textarea::placeholder {{
    color: var(--dbz-text-muted) !important;
    opacity: 1 !important;
}}

[data-testid="stNumberInput"] button {{
    background-color: var(--dbz-surface-alt) !important;
    color: var(--dbz-text) !important;
    border-color: var(--dbz-border) !important;
}}

[data-baseweb="select"] svg,
[data-baseweb="datepicker"] svg {{
    fill: var(--dbz-text-sec) !important;
    color: var(--dbz-text-sec) !important;
}}

[data-baseweb="popover"],
[data-baseweb="popover"] > div,
[role="listbox"],
[role="listbox"] li,
[role="option"],
.react-aria-Popover,
.react-aria-Popover > div {{
    background-color: var(--dbz-surface-elevated) !important;
    color: var(--dbz-text) !important;
    border-color: var(--dbz-border) !important;
}}

[role="listbox"] li:hover,
[role="option"]:hover,
[role="option"][aria-selected="true"] {{
    background-color: var(--dbz-surface-alt) !important;
    color: var(--dbz-text) !important;
}}

/* Calendar Popover in Dark/Light */
[data-baseweb="calendar"],
[data-baseweb="calendar"] div,
[data-baseweb="calendar"] header,
[data-baseweb="calendar"] button,
.react-aria-Calendar,
.react-aria-Calendar div,
.react-aria-Calendar header,
.react-aria-Calendar button,
.react-aria-Calendar [role="gridcell"] {{
    background-color: var(--dbz-surface-elevated) !important;
    color: var(--dbz-text) !important;
}}

[data-baseweb="calendar"] button:hover,
.react-aria-Calendar button:hover,
.react-aria-Calendar [role="gridcell"]:hover {{
    background-color: var(--dbz-surface-alt) !important;
}}

[data-baseweb="calendar"] [aria-selected="true"],
[data-baseweb="calendar"] [aria-selected="true"] div,
.react-aria-Calendar [aria-selected="true"],
.react-aria-Calendar [data-selected="true"] {{
    background-color: var(--dbz-accent) !important;
    color: var(--dbz-accent-ink) !important;
}}

/* Checkboxes, Toggles, Tabs, Segmented Control */
[data-testid="stCheckbox"] label,
[data-testid="stCheckbox"] span,
[data-testid="stToggle"] label,
[data-testid="stToggle"] span {{
    color: var(--dbz-text) !important;
}}

[data-testid="stTabs"] [role="tablist"] {{
    border-bottom: 1px solid var(--dbz-border) !important;
    gap: 0.25rem;
}}

[data-testid="stTabs"] [role="tab"] {{
    color: var(--dbz-text-sec) !important;
    font-weight: 600 !important;
}}

[data-testid="stTabs"] [role="tab"][aria-selected="true"] {{
    color: var(--dbz-accent) !important;
}}

/* ── Dialogs / Modals ── */
[data-testid="stDialog"] [role="dialog"],
[role="dialog"] {{
    background-color: var(--dbz-surface) !important;
    color: var(--dbz-text) !important;
    border: 1px solid var(--dbz-border) !important;
    border-radius: 14px !important;
    box-shadow: var(--dbz-shadow-lg) !important;
}}

[role="dialog"] h1,
[role="dialog"] h2,
[role="dialog"] h3,
[role="dialog"] h4,
[role="dialog"] p,
[role="dialog"] label,
[role="dialog"] span {{
    color: var(--dbz-text);
}}

[role="dialog"] button[aria-label="Close"] {{
    color: var(--dbz-text-sec) !important;
    background-color: var(--dbz-surface-alt) !important;
    border-radius: 8px !important;
}}

@media (max-width: 640px) {{
    [role="dialog"] {{
        width: calc(100vw - 1.1rem) !important;
        max-width: calc(100vw - 1.1rem) !important;
        margin: 0.55rem auto !important;
    }}
}}

/* ── Buttons (descendant selector handles tooltip wrappers cleanly) ── */
.stButton button,
.stDownloadButton button,
[data-testid="stFormSubmitButton"] button {{
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.84rem !important;
    min-height: 2.35rem !important;
    padding: 0.4rem 0.7rem !important;
    border: 1px solid var(--dbz-border-strong) !important;
    background-color: var(--dbz-surface) !important;
    color: var(--dbz-text) !important;
    box-shadow: var(--dbz-shadow-sm);
    transition: border-color 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}}

.stButton button p,
.stDownloadButton button p,
[data-testid="stFormSubmitButton"] button p {{
    color: inherit !important;
    font-size: 0.84rem !important;
    font-weight: 600 !important;
    margin: 0 !important;
    white-space: nowrap !important;
}}

.stButton button:focus-visible,
.stDownloadButton button:focus-visible,
[data-testid="stFormSubmitButton"] button:focus-visible {{
    outline: 2px solid var(--dbz-accent) !important;
    outline-offset: 2px !important;
}}

.stButton button:hover,
.stDownloadButton button:hover,
[data-testid="stFormSubmitButton"] button:hover {{
    border-color: var(--dbz-accent) !important;
    color: var(--dbz-accent) !important;
    background-color: var(--dbz-surface-elevated) !important;
}}

.stButton button[kind="primary"],
.stButton button[kind="primaryFormSubmit"],
.stDownloadButton button[kind="primary"],
[data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-primaryFormSubmit"],
[data-testid="stFormSubmitButton"] button[kind="primary"],
[data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"] {{
    background-color: var(--dbz-accent) !important;
    border-color: var(--dbz-accent) !important;
    color: var(--dbz-accent-ink) !important;
}}

.stButton button[kind="primary"] *,
.stButton button[kind="primaryFormSubmit"] *,
.stDownloadButton button[kind="primary"] *,
[data-testid="stBaseButton-primary"] *,
[data-testid="stBaseButton-primaryFormSubmit"] *,
[data-testid="stFormSubmitButton"] button[kind="primary"] *,
[data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"] * {{
    color: var(--dbz-accent-ink) !important;
    background-color: transparent !important;
}}

.stButton button[kind="primary"]:hover,
.stButton button[kind="primaryFormSubmit"]:hover,
.stDownloadButton button[kind="primary"]:hover,
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-primaryFormSubmit"]:hover,
[data-testid="stFormSubmitButton"] button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"]:hover {{
    background-color: var(--dbz-accent-hover) !important;
    border-color: var(--dbz-accent-hover) !important;
    color: var(--dbz-accent-ink) !important;
}}

.stButton button:disabled,
.stDownloadButton button:disabled,
[data-testid="stFormSubmitButton"] button:disabled,
.stButton button[kind="primary"]:disabled {{
    background-color: var(--dbz-disabled-bg) !important;
    border-color: var(--dbz-border) !important;
    color: var(--dbz-disabled-text) !important;
    opacity: 1 !important;
    box-shadow: none !important;
    cursor: not-allowed !important;
}}

.stButton button:disabled *,
[data-testid="stFormSubmitButton"] button:disabled * {{
    color: var(--dbz-disabled-text) !important;
}}

/* ── Bottom Chat Bar & Composer ── */
[data-testid="stBottom"],
[data-testid="stBottom"] > div {{
    background-color: var(--dbz-bg) !important;
    border-top: 1px solid var(--dbz-border) !important;
}}

[data-testid="stChatInput"],
[data-testid="stChatInput"] > div {{
    background-color: var(--dbz-surface) !important;
    border-color: var(--dbz-border-strong) !important;
    border-radius: 12px !important;
}}

[data-testid="stChatInput"] {{
    border: 1px solid var(--dbz-border-strong) !important;
    box-shadow: var(--dbz-shadow) !important;
}}

[data-testid="stChatInput"] textarea {{
    background-color: transparent !important;
    color: var(--dbz-text) !important;
    min-height: 24px !important;
}}

[data-testid="stChatInputSubmitButton"] button {{
    background-color: var(--dbz-accent) !important;
    color: var(--dbz-accent-ink) !important;
    border-radius: 8px !important;
    border: none !important;
}}

[data-testid="stChatInputSubmitButton"] button:disabled {{
    background-color: var(--dbz-disabled-bg) !important;
    color: var(--dbz-disabled-text) !important;
}}

/* ── Expanders & Alerts ── */
[data-testid="stExpander"],
[data-testid="stExpander"] details,
[data-testid="stExpander"] summary {{
    background-color: var(--dbz-surface) !important;
    color: var(--dbz-text) !important;
    border-color: var(--dbz-border) !important;
    border-radius: 10px !important;
}}

[data-testid="stExpander"] summary:hover {{
    background-color: var(--dbz-surface-alt) !important;
}}

[data-testid="stExpander"] summary span,
[data-testid="stExpander"] summary p,
[data-testid="stExpander"] summary svg {{
    color: var(--dbz-text) !important;
    fill: var(--dbz-text-sec) !important;
}}

[data-testid="stAlert"],
[data-testid="stAlertContainer"] {{
    background-color: var(--dbz-surface) !important;
    color: var(--dbz-text) !important;
    border: 1px solid var(--dbz-border) !important;
    border-radius: 10px !important;
}}

[data-testid="stAlert"] p,
[data-testid="stAlert"] div {{
    color: var(--dbz-text) !important;
}}

/* ── Brand & Section Headers ── */
.dbz-brand {{
    display: flex;
    align-items: baseline;
    gap: 0.45rem;
    padding: 0.15rem 0 0.7rem 0;
    border-bottom: 1px solid var(--dbz-border);
    margin-bottom: 0.65rem;
}}

.dbz-wordmark {{
    font-size: 1.4rem;
    font-weight: 800;
    letter-spacing: -0.03em;
    color: var(--dbz-accent) !important;
}}

.dbz-submark {{
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--dbz-text-sec) !important;
    letter-spacing: 0.01em;
}}

.dbz-section-label {{
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--dbz-text-sec) !important;
    margin: 0.95rem 0 0.4rem 0;
}}

/* ── Top Utility Workspace Bar ── */
.dbz-topbar {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.65rem;
    background-color: var(--dbz-surface);
    border: 1px solid var(--dbz-border);
    border-radius: 12px;
    padding: 0.7rem 1rem;
    margin-bottom: 0.65rem;
    box-shadow: var(--dbz-shadow-sm);
}}

.dbz-topbar-left {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.55rem;
}}

.dbz-topbar-title {{
    font-size: 0.94rem;
    font-weight: 700;
    color: var(--dbz-text);
}}

.dbz-topbar-meta {{
    font-size: 0.8rem;
    color: var(--dbz-text-sec);
    font-variant-numeric: tabular-nums;
}}

.dbz-topbar-dot {{
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background-color: var(--dbz-success);
    display: inline-block;
}}

.dbz-topbar-dot.degraded {{
    background-color: var(--dbz-warning);
}}

/* Responsive 4-col Toolbar (desktop) -> 2x2 (<=1080px) */
[data-testid="stElementContainer"]:has(.dbz-toolbar-marker) + [data-testid="stHorizontalBlock"] {{
    display: grid !important;
    grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
    gap: 0.5rem !important;
    margin-bottom: 0.75rem !important;
}}

[data-testid="stElementContainer"]:has(.dbz-toolbar-marker) + [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
    width: 100% !important;
    min-width: 0 !important;
    flex: none !important;
}}

@media (max-width: 1080px) {{
    [data-testid="stElementContainer"]:has(.dbz-toolbar-marker) + [data-testid="stHorizontalBlock"] {{
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    }}
}}

/* ── Welcome / Discovery Hero (Balanced, High-Craft) ── */
.dbz-hero {{
    background-color: var(--dbz-surface);
    border: 1px solid var(--dbz-border);
    border-radius: 14px;
    padding: 1.55rem 1.7rem;
    margin-bottom: 1rem;
    box-shadow: var(--dbz-shadow);
}}

@media (max-width: 640px) {{
    .dbz-hero {{
        padding: 1.15rem 1.1rem;
    }}
}}

.dbz-hero-kicker {{
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--dbz-accent);
    margin-bottom: 0.35rem;
}}

.dbz-hero-title {{
    font-size: clamp(1.32rem, 2.2vw, 1.72rem);
    font-weight: 750;
    line-height: 1.22;
    letter-spacing: -0.02em;
    color: var(--dbz-text);
    margin: 0 0 0.5rem 0;
}}

.dbz-hero-desc {{
    font-size: 0.94rem;
    line-height: 1.55;
    color: var(--dbz-text-sec);
    margin: 0 0 0.85rem 0;
    max-width: 68ch;
}}

.dbz-hero-stats {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.25rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--dbz-border);
    font-size: 0.8rem;
    color: var(--dbz-text-sec);
    font-variant-numeric: tabular-nums;
}}

.dbz-hero-stat strong {{
    color: var(--dbz-text);
    font-weight: 700;
}}

/* ── Returning Buyer Continuity Card ── */
.dbz-returning-banner {{
    background-color: var(--dbz-surface);
    border: 1px solid var(--dbz-border);
    border-left: 3px solid var(--dbz-accent);
    border-radius: 12px;
    padding: 0.95rem 1.15rem;
    margin-bottom: 0.85rem;
    box-shadow: var(--dbz-shadow-sm);
}}

.dbz-returning-eyebrow {{
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--dbz-accent);
    margin-bottom: 0.25rem;
}}

.dbz-returning-text {{
    font-size: 0.91rem;
    line-height: 1.48;
    color: var(--dbz-text);
}}

/* ── Conversational Messages (Distinct User vs Assistant) ── */
.dbz-msg-user-card {{
    background-color: var(--dbz-chat-user);
    border: 1px solid var(--dbz-border);
    border-radius: 12px;
    padding: 0.85rem 1.05rem;
    margin: 0.45rem 0 0.5rem auto;
    max-width: min(86%, 760px);
    color: var(--dbz-text);
}}

@media (max-width: 640px) {{
    .dbz-msg-user-card {{
        max-width: 96%;
    }}
}}

.dbz-msg-user-body {{
    font-size: 0.94rem;
    line-height: 1.5;
    color: var(--dbz-text);
    white-space: pre-wrap;
    word-break: break-word;
}}

[data-testid="stLayoutWrapper"]:has(.dbz-msg-assistant-marker) {{
    background-color: var(--dbz-chat-assistant) !important;
    border: 1px solid var(--dbz-border) !important;
    border-left: 3px solid var(--dbz-accent) !important;
    border-radius: 12px !important;
    padding: 0.85rem 1.05rem !important;
    margin: 0.25rem 0 0.65rem 0 !important;
    box-shadow: var(--dbz-shadow-sm);
}}

.dbz-msg-header {{
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--dbz-text-sec);
    margin-bottom: 0.28rem;
}}

.dbz-msg-header.assistant {{
    color: var(--dbz-accent);
}}

/* ── Search Transparency Strip ── */
.dbz-transparency-bar {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.4rem 0.9rem;
    background-color: var(--dbz-surface-alt);
    border: 1px solid var(--dbz-border);
    border-radius: 10px;
    padding: 0.55rem 0.9rem;
    margin: 0.25rem 0 0.65rem 0;
    font-size: 0.8125rem;
    color: var(--dbz-text-sec);
    font-variant-numeric: tabular-nums;
}}

.dbz-transparency-bar strong {{
    color: var(--dbz-text);
}}

/* ── Focused Vehicle Context Strip ── */
.dbz-focus-strip {{
    background-color: var(--dbz-surface);
    border: 1px solid var(--dbz-border);
    border-left: 3px solid var(--dbz-accent);
    border-radius: 10px;
    padding: 0.65rem 0.95rem;
    margin: 0.6rem 0 0.45rem 0;
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 0.5rem;
}}

.dbz-focus-title {{
    font-size: 0.74rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--dbz-accent);
}}

.dbz-focus-meta {{
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--dbz-text);
}}

/* ── Vehicle Cards (Grid + Horizontal Single Showcase) ── */
[data-testid="stHorizontalBlock"]:has(.dbz-card) {{
    align-items: stretch !important;
}}

@media (max-width: 1120px) {{
    [data-testid="stHorizontalBlock"]:has(.dbz-card) {{
        flex-direction: column !important;
        gap: 0.85rem !important;
    }}
    [data-testid="stHorizontalBlock"]:has(.dbz-card) > [data-testid="stColumn"] {{
        width: 100% !important;
        flex: 1 1 100% !important;
    }}
}}

/* Keep card action buttons side-by-side even on narrow mobile (<640px) */
[data-testid="stElementContainer"]:has(.dbz-keep-2col) + [data-testid="stHorizontalBlock"] {{
    display: grid !important;
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    gap: 0.45rem !important;
    margin-top: 0.35rem !important;
}}

[data-testid="stElementContainer"]:has(.dbz-keep-3col) + [data-testid="stHorizontalBlock"] {{
    display: grid !important;
    grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
    gap: 0.45rem !important;
    margin-top: 0.35rem !important;
    margin-bottom: 0.75rem !important;
}}

[data-testid="stElementContainer"]:has(.dbz-keep-2col) + [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
[data-testid="stElementContainer"]:has(.dbz-keep-3col) + [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
    width: 100% !important;
    min-width: 0 !important;
    flex: none !important;
}}

.dbz-card {{
    background-color: var(--dbz-surface);
    border: 1px solid var(--dbz-border);
    border-radius: 12px;
    overflow: hidden;
    box-shadow: var(--dbz-shadow);
    margin-bottom: 0.35rem;
}}

.dbz-card.dbz-card-horizontal {{
    display: grid;
    grid-template-columns: minmax(220px, 310px) 1fr;
}}

@media (max-width: 720px) {{
    .dbz-card.dbz-card-horizontal {{
        grid-template-columns: 1fr;
    }}
}}

.dbz-card-img-wrap {{
    position: relative;
    width: 100%;
    aspect-ratio: 16 / 10;
    background-color: var(--dbz-placeholder-bg);
    overflow: hidden;
    border-bottom: 1px solid var(--dbz-border);
}}

.dbz-card-horizontal .dbz-card-img-wrap {{
    height: 100%;
    min-height: 205px;
    border-bottom: none;
    border-right: 1px solid var(--dbz-border);
}}

@media (max-width: 720px) {{
    .dbz-card-horizontal .dbz-card-img-wrap {{
        min-height: 0;
        border-right: none;
        border-bottom: 1px solid var(--dbz-border);
    }}
}}

.dbz-card-img-wrap img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}}

.dbz-card-id-badge {{
    position: absolute;
    top: 10px;
    left: 10px;
    background-color: rgba(16, 18, 24, 0.80);
    color: #FFFFFF;
    font-size: 0.75rem;
    font-weight: 600;
    padding: 0.18rem 0.52rem;
    border-radius: 6px;
    font-variant-numeric: tabular-nums;
}}

.dbz-card-lang-badge {{
    position: absolute;
    top: 10px;
    right: 10px;
    background-color: rgba(16, 18, 24, 0.78);
    color: #FFFFFF;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 0.16rem 0.45rem;
    border-radius: 6px;
}}

.dbz-card-body {{
    padding: 0.95rem 1.05rem 0.9rem 1.05rem;
}}

.dbz-card-title {{
    font-size: 1.02rem;
    font-weight: 700;
    line-height: 1.3;
    color: var(--dbz-text);
    margin: 0 0 0.18rem 0;
}}

.dbz-card-trim {{
    font-size: 0.8125rem;
    color: var(--dbz-text-sec);
    margin-bottom: 0.55rem;
    min-height: 1.1rem;
}}

.dbz-card-price-row {{
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.45rem 0.7rem;
    margin-bottom: 0.15rem;
}}

.dbz-card-price {{
    font-size: 1.15rem;
    font-weight: 750;
    color: var(--dbz-accent);
    font-variant-numeric: tabular-nums;
}}

.dbz-card-price.unlisted {{
    color: var(--dbz-text-sec);
    font-size: 0.96rem;
    font-weight: 600;
}}

.dbz-card-price.conflict {{
    color: var(--dbz-warning);
    font-size: 0.94rem;
    font-weight: 650;
}}

.dbz-card-monthly {{
    font-size: 0.78rem;
    color: var(--dbz-text-sec);
    margin-bottom: 0.55rem;
    min-height: 1.05rem;
    font-variant-numeric: tabular-nums;
}}

.dbz-chips {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    margin: 0.45rem 0 0.35rem 0;
}}

.dbz-chip {{
    display: inline-flex;
    align-items: center;
    background-color: var(--dbz-surface-alt);
    border: 1px solid var(--dbz-border);
    color: var(--dbz-text);
    font-size: 0.75rem;
    font-weight: 500;
    padding: 0.2rem 0.52rem;
    border-radius: 999px;
    font-variant-numeric: tabular-nums;
}}

.dbz-chip.warn {{
    background-color: var(--dbz-warning-soft);
    border-color: transparent;
    color: var(--dbz-warning);
    font-weight: 600;
}}

.dbz-chip.ok {{
    background-color: var(--dbz-success-soft);
    border-color: transparent;
    color: var(--dbz-success);
    font-weight: 600;
}}

.dbz-chip.muted {{
    background-color: transparent;
    border-style: dashed;
    border-color: var(--dbz-border-strong);
    color: var(--dbz-text-sec);
}}

.dbz-match-reason {{
    font-size: 0.78rem;
    color: var(--dbz-text-sec);
    border-top: 1px solid var(--dbz-border);
    padding-top: 0.5rem;
    margin-top: 0.45rem;
    line-height: 1.4;
}}

/* ── Status Pills & Semantic Badges ── */
.dbz-pill {{
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.16rem 0.52rem;
    border-radius: 999px;
}}

.dbz-pill.confirmed, .dbz-pill.hot {{
    background-color: var(--dbz-success-soft);
    color: var(--dbz-success);
}}

.dbz-pill.proposed, .dbz-pill.warm, .dbz-pill.inferred {{
    background-color: var(--dbz-warning-soft);
    color: var(--dbz-warning);
}}

.dbz-pill.rejected {{
    background-color: var(--dbz-danger-soft);
    color: var(--dbz-danger);
}}

.dbz-pill.cold, .dbz-pill.info {{
    background-color: var(--dbz-info-soft);
    color: var(--dbz-info);
}}

.dbz-pill.neutral {{
    background-color: var(--dbz-surface-alt);
    color: var(--dbz-text-sec);
}}

/* ── Booking Stepper & Cards ── */
.dbz-stepper {{
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.5rem;
    margin-bottom: 0.9rem;
}}

.dbz-step {{
    background-color: var(--dbz-surface-alt);
    border: 1px solid var(--dbz-border);
    border-radius: 8px;
    padding: 0.45rem 0.65rem;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--dbz-text-sec);
    text-align: center;
}}

.dbz-step.active {{
    background-color: var(--dbz-accent-soft);
    border-color: var(--dbz-accent);
    color: var(--dbz-accent);
}}

.dbz-step.done {{
    background-color: var(--dbz-success-soft);
    border-color: transparent;
    color: var(--dbz-success);
}}

.dbz-booking-box {{
    background-color: var(--dbz-surface-alt);
    border: 1px solid var(--dbz-border);
    border-radius: 12px;
    padding: 1rem 1.15rem;
    margin: 0.75rem 0 0.85rem 0;
}}

.dbz-booking-title {{
    font-size: 1.08rem;
    font-weight: 750;
    color: var(--dbz-text);
    margin: 0.5rem 0 0.35rem 0;
}}

/* ── Details Modal Specs & Description ── */
.dbz-spec-grid {{
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.55rem;
    margin: 0.5rem 0 0.85rem 0;
}}

@media (max-width: 640px) {{
    .dbz-spec-grid {{
        grid-template-columns: 1fr;
    }}
}}

.dbz-spec-cell {{
    background-color: var(--dbz-surface-alt);
    border: 1px solid var(--dbz-border);
    border-radius: 9px;
    padding: 0.55rem 0.75rem;
}}

.dbz-spec-k {{
    font-size: 0.74rem;
    font-weight: 600;
    color: var(--dbz-text-sec);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 0.15rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.35rem;
}}

.dbz-spec-v {{
    font-size: 0.9rem;
    font-weight: 650;
    color: var(--dbz-text);
    font-variant-numeric: tabular-nums;
}}

.dbz-spec-v.missing {{
    color: var(--dbz-text-muted);
    font-weight: 500;
}}

.dbz-spec-ev {{
    font-size: 0.75rem;
    color: var(--dbz-text-sec);
    margin-top: 0.22rem;
    line-height: 1.35;
}}

.dbz-desc-box {{
    background-color: var(--dbz-surface-alt);
    border: 1px solid var(--dbz-border);
    border-radius: 10px;
    padding: 0.85rem 1rem;
    font-size: 0.88rem;
    line-height: 1.55;
    color: var(--dbz-text);
    max-height: 260px;
    overflow-y: auto;
    word-break: break-word;
}}

/* ── Comparison Matrix Table ── */
.dbz-compare-scroll {{
    width: 100%;
    overflow-x: auto;
    border: 1px solid var(--dbz-border);
    border-radius: 10px;
    margin: 0.5rem 0 0.75rem 0;
}}

.dbz-compare-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.84rem;
    background-color: var(--dbz-surface);
    color: var(--dbz-text);
}}

.dbz-compare-table th,
.dbz-compare-table td {{
    border-bottom: 1px solid var(--dbz-border);
    border-right: 1px solid var(--dbz-border);
    padding: 0.55rem 0.7rem;
    text-align: left;
    vertical-align: top;
    font-variant-numeric: tabular-nums;
}}

.dbz-compare-table th:last-child,
.dbz-compare-table td:last-child {{
    border-right: none;
}}

.dbz-compare-table tr:last-child td {{
    border-bottom: none;
}}

.dbz-compare-table thead th {{
    background-color: var(--dbz-surface-alt);
    font-weight: 700;
    color: var(--dbz-text);
    min-width: 155px;
}}

.dbz-compare-table tbody th {{
    background-color: var(--dbz-surface-alt);
    font-weight: 600;
    color: var(--dbz-text-sec);
    width: 145px;
    min-width: 125px;
}}

.dbz-val-missing {{
    color: var(--dbz-text-muted);
    font-style: italic;
}}

.dbz-val-price {{
    color: var(--dbz-accent);
    font-weight: 750;
}}

/* ── Sidebar Items & Empty States ── */
.dbz-sidebar-item {{
    background-color: var(--dbz-surface);
    border: 1px solid var(--dbz-border);
    border-radius: 8px;
    padding: 0.55rem 0.68rem;
    margin-bottom: 0.42rem;
    font-size: 0.82rem;
    line-height: 1.4;
    color: var(--dbz-text) !important;
}}

.dbz-sidebar-item strong {{
    color: var(--dbz-text) !important;
}}

.dbz-sidebar-sub {{
    color: var(--dbz-text-sec) !important;
    font-size: 0.76rem;
}}

.dbz-empty-state {{
    background-color: var(--dbz-surface-alt);
    border: 1px dashed var(--dbz-border-strong);
    border-radius: 10px;
    padding: 0.9rem 1rem;
    margin: 0.45rem 0;
}}

.dbz-empty-title {{
    font-size: 0.88rem;
    font-weight: 700;
    color: var(--dbz-text);
    margin-bottom: 0.18rem;
}}

.dbz-empty-body {{
    font-size: 0.82rem;
    color: var(--dbz-text-sec);
    line-height: 1.45;
}}
</style>
"""
