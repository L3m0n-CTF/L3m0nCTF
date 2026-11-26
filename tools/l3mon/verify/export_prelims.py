"""READ-ONLY export of the qualifier participants, run INSIDE the platform container (it uses the platform's own code and changes nothing):

    docker exec -i "$(tools/compose.sh ps -q ctfd)" bash -c "source /usr/local/lib/l3mon-env.sh; python -" \
        < tools/l3mon/verify/export_prelims.py > /tmp/prelims-export.txt

Who is in it: every member of every studio the public scoreboard ranks (CTFd's public standings: hidden and banned studios left out, then only
the studios above 0 TRP, exactly as the scoreboard page and the CTFtime feed count them: plugins/l3mon_core/standings.py), minus banned or
hidden accounts and crew accounts. Run it AFTER the qualifiers have ended and any Revoke/Restore is settled.

It prints the CSV between two marker lines (CTFd prints its own start-up lines too); `python -m l3mon verify issue --prelims` reads the part
between the markers. Columns: user_id, username, studio, prelims_rank (the rank is kept for the crew's own checks; it is not shown anywhere).
The output holds usernames and studio names: keep it private and delete it after the issue run.
"""
import csv
import io
import sys

from CTFd import create_app

app = create_app()
with app.app_context():
    from CTFd.models import Teams
    from CTFd.utils.scores import get_standings
    from CTFd.plugins.l3mon_core.standings import ranked

    buf = io.StringIO()
    out = csv.writer(buf, lineterminator="\n")
    out.writerow(["user_id", "username", "studio", "prelims_rank"])
    studios = people = 0
    for position, row in ranked(get_standings(admin=False)):
        team = Teams.query.filter_by(id=row.account_id).first()
        if team is None:
            continue
        studios += 1
        for user in sorted(team.members, key=lambda u: u.id):
            if user.banned or user.hidden or user.type == "admin":
                continue
            out.writerow([user.id, user.name, team.name, position])
            people += 1
    sys.stdout.write("-----BEGIN L3MON PRELIMS EXPORT-----\n" + buf.getvalue() + "-----END L3MON PRELIMS EXPORT-----\n")
    sys.stderr.write(f"{studios} ranked studios, {people} participants exported\n")
