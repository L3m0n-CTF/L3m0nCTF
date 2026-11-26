"""The participant verification portal (verify.<domain>): codes, the credentials database and the static website.

Every person who took part gets one code, `L3M0N26-<CAT>-<5 letters>`:

    ORG  an organiser of L3m0n CTF 2026             (from a crew list of names)
    PAR  a member of a studio that scored above 0 TRP in the online qualifiers   (from the platform, read-only export)
    ACH  a member of a top-3 studio of the finals   (from the finals results)
    APP  a member of any other finals studio        (from the finals results)

The portal is a STATIC website: `python -m l3mon verify build` writes one ready-made page per code from the credentials database, and nginx serves the
files (deploy/nginx/verify/verify.conf). There is no application on the server, nothing to log in to and nothing that writes, so the site can run for
years and move to any static host unchanged. Issuing codes is a crew job done off the server:

    python -m l3mon verify init    DB                           an empty database with the agreed schema
    python -m l3mon verify issue   DB --org FILE --prelims CSV --finals CSV --date YYYY-MM-DD [--reissue WITHDRAWN-CODE]
    python -m l3mon verify withdraw DB CODE --reason TEXT
    python -m l3mon verify list    DB
    python -m l3mon verify build   DB OUT --sponsors DIR

The database and the sponsor folder are PRIVATE data (real organiser names): they never go into this public repository.
Standard library only.
"""
