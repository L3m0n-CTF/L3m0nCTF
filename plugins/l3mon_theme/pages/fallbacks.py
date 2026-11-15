"""Forms of the theme that CTFd has no route for (found on 2026-11-14 by the superior's first test).

Sign out: the theme's button is a form that POSTs the page's nonce to /logout, so another site cannot sign a player out with an image or a link. CTFd answers
/logout only for GET (and its CSRF check turned a POST into "not found"), so the button did nothing. CTFd's own GET /logout stays as it is.

The flag form of the board: its script sends the flag to the API; sent without the script (not loaded yet, blocked, failed) the browser posts the form to /board,
which answered "not found". The form cannot carry a flag to the right programme without its script, so the player goes back to the board page.

Both POSTs pass CTFd's own CSRF check (the nonce of the session) before these views run.

Addresses the platform has no page for but people and programs ask for (found by the end-to-end test of 14-15 November): /privacy and /rules (the
registration form links to /privacy; both pages live on the website) go to the website's pages; /favicon.ico (browsers ask for it by themselves) goes
to the icon the platform has.
"""
from flask import redirect

from CTFd.plugins.l3mon_theme import values


def install(app):
    from CTFd.utils.security.auth import logout_user
    from CTFd.utils.user import authed

    def sign_out():
        if authed():
            logout_user()
        return redirect("/", code=303)

    def back_to_the_board():
        return redirect("/board", code=303)

    def to_website(path):
        def go():
            return redirect(values.site_url().rstrip("/") + path, code=302)

        return go

    app.add_url_rule("/rules", endpoint="l3mon_theme_rules_on_the_website", view_func=to_website("/rules/"), methods=["GET", "HEAD"])
    # CTFd has its own /privacy (it answers "not found" until a privacy text is set in its settings): the rule of the same address
    # would never be reached, so its view is replaced
    if "views.privacy" in app.view_functions:
        app.view_functions["views.privacy"] = to_website("/privacy/")
    else:
        app.add_url_rule("/privacy", endpoint="l3mon_theme_privacy_on_the_website", view_func=to_website("/privacy/"), methods=["GET", "HEAD"])
    app.add_url_rule("/favicon.ico", endpoint="l3mon_theme_favicon_ico", view_func=lambda: redirect("/favicon.svg", code=302), methods=["GET", "HEAD"])
    app.add_url_rule("/logout", endpoint="l3mon_theme_sign_out", view_func=sign_out, methods=["POST"])
    # only when the board page itself is installed (pages/board.py runs before this module): an extra rule on /board would otherwise stop the
    # redirect alias of pages/routes.py from stepping in for a missing board page
    if any(rule.rule == "/board" and "GET" in (rule.methods or ()) for rule in app.url_map.iter_rules()):
        app.add_url_rule("/board", endpoint="l3mon_theme_board_without_script", view_func=back_to_the_board, methods=["POST"])
