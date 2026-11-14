"""Forms of the theme that CTFd has no route for (found on 2026-11-14 by the superior's first test).

Sign out: the theme's button is a form that POSTs the page's nonce to /logout, so another site cannot sign a player out with an image or a link. CTFd answers
/logout only for GET (and its CSRF check turned a POST into "not found"), so the button did nothing. CTFd's own GET /logout stays as it is.

The flag form of the board: its script sends the flag to the API; sent without the script (not loaded yet, blocked, failed) the browser posts the form to /board,
which answered "not found". The form cannot carry a flag to the right programme without its script, so the player goes back to the board page.

Both POSTs pass CTFd's own CSRF check (the nonce of the session) before these views run.
"""
from flask import redirect


def install(app):
    from CTFd.utils.security.auth import logout_user
    from CTFd.utils.user import authed

    def sign_out():
        if authed():
            logout_user()
        return redirect("/", code=303)

    def back_to_the_board():
        return redirect("/board", code=303)

    app.add_url_rule("/logout", endpoint="l3mon_theme_sign_out", view_func=sign_out, methods=["POST"])
    # only when the board page itself is installed (pages/board.py runs before this module): an extra rule on /board would otherwise stop the
    # redirect alias of pages/routes.py from stepping in for a missing board page
    if any(rule.rule == "/board" and "GET" in (rule.methods or ()) for rule in app.url_map.iter_rules()):
        app.add_url_rule("/board", endpoint="l3mon_theme_board_without_script", view_func=back_to_the_board, methods=["POST"])
