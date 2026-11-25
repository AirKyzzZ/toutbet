from html import escape


def page(title: str, *body: str) -> str:
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(title)}</title><link rel="stylesheet" href="/assets/mock.css">'
        f'</head><body><main><p class="tag">PSP de test</p><h1>{escape(title)}</h1>'
        + "".join(body)
        + "</main></body></html>"
    )


def paragraph(text: str) -> str:
    return f"<p>{escape(text)}</p>"


def button_form(action: str, label: str, css_class: str = "") -> str:
    return (
        f'<form method="post" action="{escape(action)}">'
        f'<button type="submit" class="{escape(css_class)}">{escape(label)}</button></form>'
    )


def back_link(url: str) -> str:
    return f'<p><a href="{escape(url)}">Retour à ToutBet\'</a></p>'


STYLESHEET = """
body { font-family: system-ui, sans-serif; background: #f5f5f4; margin: 0; }
main { max-width: 28rem; margin: 2rem auto; background: #fff; padding: 1.5rem;
  border-radius: 1rem; }
.tag { color: #92400e; font-weight: 700; }
form { margin: 0.75rem 0; }
button { width: 100%; padding: 0.8rem; font: inherit; border: none; border-radius: 0.5rem;
  background: #1d4ed8; color: #fff; }
button.refuse { background: #b91c1c; }
"""
