"""
Bigdoc — Module de sécurité formulaires
- Honeypot anti-bot
- Blocage domaines email jetables
- Rate limiting par email (désactivable en dev via RATE_LIMIT_ENABLED=false)
"""
import os
import re
import time
import logging
from collections import defaultdict

logger = logging.getLogger("bigdoc")

# ─── CONFIG ───────────────────────────────────────────────────────────────────
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() != "false"
RATE_LIMIT_MAX     = int(os.getenv("RATE_LIMIT_EMAIL_MAX", "3"))   # max diagnostics par email
RATE_LIMIT_WINDOW  = int(os.getenv("RATE_LIMIT_EMAIL_WINDOW", "86400"))  # fenêtre en secondes (24h)

# ─── DOMAINES JETABLES ────────────────────────────────────────────────────────
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamailblock.com",
    "grr.la", "guerrillamail.info", "guerrillamail.biz", "guerrillamail.de",
    "guerrillamail.net", "guerrillamail.org", "spam4.me",
    "yopmail.com", "yopmail.fr", "cool.fr.nf", "jetable.fr.nf",
    "nospam.ze.tc", "nomail.xl.cx", "mega.zik.dj", "speed.1s.fr",
    "courriel.fr.nf", "moncourrier.fr.nf", "monemail.fr.nf",
    "monmail.fr.nf", "mail-temporaire.fr",
    "10minutemail.com", "10minutemail.net", "10minutemail.org",
    "10minutemail.de", "10minutemail.info", "10minutemail.co.uk",
    "10minutemail.us", "10minutemail.cf", "10minutemail.ga",
    "10minutemail.gq", "10minutemail.ml", "10minutemail.tk",
    "trashmail.com", "trashmail.me", "trashmail.net", "trashmail.org",
    "trashmail.at", "trashmail.io", "trashmail.xyz",
    "dispostable.com", "disposablemail.com", "throwam.com",
    "fakeinbox.com", "mailnull.com", "spamgourmet.com",
    "spamgourmet.net", "spamgourmet.org", "spamex.com",
    "spamfree24.org", "spamfree24.de", "spamfree24.eu",
    "spamfree24.info", "spamfree24.net", "spamfree24.com",
    "spamhole.com", "spamify.com", "spaminator.de",
    "tempmail.com", "tempmail.net", "tempmail.org", "tempmail.de",
    "temp-mail.org", "temp-mail.io", "tempinbox.com",
    "throwaway.email", "throwam.com", "discard.email",
    "mailnesia.com", "mailnull.com", "spamevader.com",
    "maildrop.cc", "mailzilla.com", "mailzilla.org",
    "sharklasers.com", "guerrillamail.info", "grr.la",
    "amilegit.com", "anonymbox.com", "antichef.com",
    "antichef.net", "antireg.ru", "antispam.de",
    "baxomale.ht.cx", "beefmilk.com", "binkmail.com",
    "bobmail.info", "bodhi.lawlita.com", "bofthew.com",
    "bugmenot.com", "bumpymail.com", "casualdx.com",
    "cheatmail.de", "chong-mail.net", "clixser.com",
    "crapmail.org", "cust.in", "dakuan.com",
    "deadaddress.com", "deadletter.ga", "despam.it",
    "devnullmail.com", "dfgh.net", "digitalsanctuary.com",
    "discardmail.com", "discardmail.de", "dodgeit.com",
    "dodgit.com", "donemail.ru", "dontreg.com",
    "dontsendmespam.de", "dump-email.info", "dumpmail.de",
    "dumpyemail.com", "e4ward.com", "email60.com",
    "emailias.com", "emailinfive.com", "emailmiser.com",
    "emailsensei.com", "emailtemporario.com.br", "emailthe.net",
    "emailtmp.com", "emailwarden.com", "emailx.at.hm",
    "emailxfer.com", "emz.net", "esc.la",
    "explodemail.com", "express.net.ua", "eyepaste.com",
    "fastacura.com", "filzmail.com", "fivemail.de",
    "fleckens.hu", "frapmail.com", "front14.org",
    "fux0ringduh.com", "garliclife.com", "get1mail.com",
    "get2mail.fr", "getonemail.com", "getonemail.net",
    "girlsundertheinfluence.com", "gishpuppy.com", "goemailgo.com",
    "gotmail.net", "gotmail.org", "gotti.otherinbox.com",
    "gowikibooks.com", "gowikicampus.com", "gowikicars.com",
    "gowikifilms.com", "gowikigames.com", "gowikimusic.com",
    "gowikinetwork.com", "gowikitravel.com", "gowikitv.com",
    "grandmasmail.com", "great-host.in", "greensloth.com",
    "gsrv.co.uk", "gustr.com", "h.mintemail.com",
    "haltospam.com", "hatespam.org", "hidemail.de",
    "hochsitze.com", "hopemail.biz", "hushmail.com",
    "ieatspam.eu", "ieatspam.info", "ieh-mail.de",
    "ignoremail.com", "ihateyoualot.info", "iheartspam.org",
    "imails.info", "inboxalias.com", "inboxclean.com",
    "inboxclean.org", "incognitomail.com", "incognitomail.net",
    "incognitomail.org", "insorg-mail.info", "instant-mail.de",
    "internet-e-mail.de", "internet-mail.org", "internetemails.net",
    "internetmailing.net", "inwind.it", "ipoo.org",
    "irish2me.com", "iwi.net", "jetable.com",
    "jetable.net", "jetable.org", "jnxjn.com",
    "jourrapide.com", "jsrsolutions.com", "kasmail.com",
    "kaspop.com", "killmail.com", "killmail.net",
    "klassmaster.com", "klzlk.com", "koszmail.pl",
    "kulturbetrieb.info", "kurzepost.de", "letthemeatspam.com",
    "lhsdv.com", "lifebyfood.com", "link2mail.net",
    "litedrop.com", "lol.ovpn.to", "lolfreak.net",
    "lookugly.com", "lortemail.dk", "lr78.com",
    "maboard.com", "mail-filter.com", "mail-temporaire.fr",
    "mailbidon.com", "mailbiz.biz", "mailblocks.com",
    "mailbucket.org", "mailc.net", "mailcat.biz",
    "mailcatch.com", "mailcker.com", "mailexpire.com",
    "mailf5.com", "mailfall.com", "mailfreeonline.com",
    "mailguard.me", "mailimate.com", "mailin8r.com",
    "mailinater.com", "mailismagic.com", "mailme.ir",
    "mailme.lv", "mailme24.com", "mailmetrash.com",
    "mailmoat.com", "mailnew.com", "mailnull.com",
    "mailorg.org", "mailpick.biz", "mailpluss.com",
    "mailrock.biz", "mailscrap.com", "mailshell.com",
    "mailsiphon.com", "mailslite.com", "mailsoftly.com",
    "mailtemp.info", "mailtome.de", "mailtothis.com",
    "mailtrash.net", "mailtv.net", "mailtv.tv",
    "mailzilla.org", "makemetheking.com", "malahov.de",
    "meltmail.com", "messagebeamer.de", "mezimages.net",
    "mfsa.ru", "mierdamail.com", "mintemail.com",
    "miriamsworld.org", "misterpinball.de", "moncourrier.fr.nf",
    "monemail.fr.nf", "monkeymail.io", "monmail.fr.nf",
    "mt2009.com", "mt2014.com", "mycard.net.ua",
    "mycleaninbox.net", "myemailboxy.com", "myfastmail.com",
    "mymail-in.net", "mymailoasis.com", "mynetstore.de",
    "mypacks.net", "mypartyclip.de", "myphantomemail.com",
    "mysamp.de", "myspaceinc.com", "myspaceinc.net",
    "myspaceinc.org", "myspacepimped.com", "mytemp.email",
    "mytempemail.com", "mytempmail.com",
    "nospamfor.us", "nospammail.net", "nospamthanks.info",
    "notmailinator.com", "nowhere.org", "nowmymail.com",
    "nwldx.com", "objectmail.com", "obobbo.com",
    "odnorazovoe.ru", "oneoffemail.com", "onewaymail.com",
    "onlatedotcom.info", "online.ms", "oopi.org",
    "opayq.com", "ordinaryamerican.net", "otherinbox.com",
    "ourklips.com", "outlawspam.com", "ovpn.to",
    "owlpic.com", "pancakemail.com", "paplease.com",
    "pepbot.com", "pfui.ru", "pimpedupmyspace.com",
    "pjjkp.com", "plexolan.de", "poczta.onet.pl",
    "politikerclub.de", "pookmail.com", "pornozone.ru",
    "postacı.ru", "ppetw.com", "proxymail.eu",
    "prtnx.com", "prtz.eu", "pubmail.io",
    "putthisinyourspamdatabase.com", "putthisinyourspamdatabase.net",
    "qq.com", "quickinbox.com", "recode.me",
    "reconmail.com", "recursor.net", "regbypass.com",
    "regbypass.comsafe-mail.net", "rejectmail.com",
    "rklips.com", "rmqkr.net", "royal.net",
    "rppkn.com", "rtrtr.com", "s0ny.net",
    "safe-mail.net", "safersignup.de", "safetymail.info",
    "safetypost.de", "saynotospams.com", "selfdestructingmail.com",
    "sendspamhere.com", "servicenode.us", "sharklasers.com",
    "shieldemail.com", "shiftmail.com", "shitmail.de",
    "shitmail.me", "shitmail.org", "shitware.nl",
    "shmeriously.com", "shortmail.net", "sibmail.com",
    "skeefmail.com", "slaskpost.se", "slopsbox.com",
    "smellfear.com", "snakemail.com", "sneakemail.com",
    "snkmail.com", "sofimail.com", "sofort-mail.de",
    "sogetthis.com", "soodonims.com", "spam.la",
    "spam.su", "spamavert.com", "spambob.net",
    "spambob.org", "spambog.com", "spambog.de",
    "spambog.ru", "spambox.info", "spambox.irishspringrealty.com",
    "spambox.us", "spamcannon.com", "spamcannon.net",
    "spamcero.com", "spamcon.org", "spamcorptastic.com",
    "spamcowboy.com", "spamcowboy.net", "spamcowboy.org",
    "spamday.com", "spamdecoy.net", "spameater.com",
    "spamex.com", "spamfree.eu", "spamgoes.in",
    "spamgourmet.com", "spamgourmet.net", "spamgourmet.org",
    "spamherelots.com", "spamherelots.com", "spamhereplease.com",
    "spamhole.com", "spamify.com", "spaminator.de",
    "spamkill.info", "spaml.com", "spaml.de",
    "spammotel.com", "spamoff.de", "spamslicer.com",
    "spamspot.com", "spamthis.co.uk", "spamthisplease.com",
    "spamtrail.com", "spamtroll.net", "speed.1s.fr",
    "spoofmail.de", "squizzy.de", "squizzy.eu",
    "squizzy.net", "ssuet.com", "startkeys.com",
    "stinkefinger.net", "streetwisemail.com", "stuffmail.de",
    "super-auswahl.de", "supergreatmail.com", "supermailer.jp",
    "superrito.com", "superstachel.de", "suremail.info",
    "svk.jp", "sweetxxx.de", "tafmail.com",
    "tagyourself.com", "teewars.org", "teleworm.com",
    "teleworm.us", "tempalias.com", "tempe-mail.com",
    "tempemail.biz", "tempemail.com", "tempemail.net",
    "tempimbox.com", "tempinbox.com", "tempinbox.net",
    "temporaryemail.net", "temporaryforwarding.com",
    "temporaryinbox.com", "temporarymail.org",
    "tempsky.com", "tempthe.net", "tempymail.com",
    "thanksnospam.info", "thecloudindex.com",
    "thisisnotmyrealemail.com", "throwam.com",
    "throwde.com", "throwme.com", "tilien.com",
    "tittbit.in", "tmlsend.com", "tmailinator.com",
    "toiea.com", "tradermail.info", "trash-mail.at",
    "trash-mail.com", "trash-mail.de", "trash-mail.ga",
    "trash-mail.io", "trash-mail.me", "trash-mail.net",
    "trash2009.com", "trashdevil.com", "trashdevil.de",
    "trashemail.de", "trashmail.at", "trashmail.com",
    "trashmail.me", "trashmail.net", "trashmail.org",
    "trashmail.xyz", "trashmailer.com", "trashymail.com",
    "trillianpro.com", "trmailbox.com", "trungtamtoeic.com",
    "turual.com", "twinmail.de", "tyldd.com",
    "uggsrock.com", "umail.net", "unids.com",
    "uroid.com", "us.af", "veryrealemail.com",
    "viditag.com", "viewcastmedia.com", "viewcastmedia.net",
    "viewcastmedia.org", "vkcode.ru",
    "webide.de", "wetrainbayarea.com", "wetrainbayarea.org",
    "wh4f.org", "whyspam.me", "wickmail.net",
    "willhackforfood.biz", "willselfdestruct.com",
    "wimsg.com", "wronghead.com", "wuzupmail.net",
    "www.e4ward.com", "www.gishpuppy.com", "www.mailinator.com",
    "wwwnew.eu", "xagloo.com", "xemaps.com",
    "xents.com", "xmaily.com", "xoxy.net",
    "xww.ro", "yapped.net", "yeah.net",
    "yep.it", "yogamaven.com", "yopmail.com",
    "yopmail.fr", "yopmail.net", "youcanwin3543.com",
    "yourdomain.com", "ypmail.webarnak.fr.eu.org",
    "yuurok.com", "z1p.biz", "za.com",
    "zippymail.info", "zoemail.com", "zoemail.net",
    "zoemail.org", "zomg.info",
}

# ─── REGEX EMAIL ──────────────────────────────────────────────────────────────
EMAIL_REGEX = re.compile(
    r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
)

# ─── RATE LIMITING PAR EMAIL ──────────────────────────────────────────────────
_email_attempts: dict[str, list[float]] = defaultdict(list)


def check_email_rate_limit(email: str) -> bool:
    """
    Retourne True si l'email est dans les limites, False si bloqué.
    Désactivé si RATE_LIMIT_ENABLED=false.
    """
    if not RATE_LIMIT_ENABLED:
        return True

    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    email_lower = email.lower()

    # Nettoyer les tentatives expirées
    _email_attempts[email_lower] = [
        t for t in _email_attempts[email_lower] if t > window_start
    ]

    if len(_email_attempts[email_lower]) >= RATE_LIMIT_MAX:
        logger.warning(f"Rate limit email: {email_lower}")
        return False

    _email_attempts[email_lower].append(now)
    return True


# ─── VALIDATION EMAIL ─────────────────────────────────────────────────────────
def validate_email_security(email: str) -> tuple[bool, str]:
    """
    Valide l'email côté sécurité.
    Retourne (ok, message_erreur).
    """
    if not email or len(email) > 254:
        return False, "Adresse email invalide."

    if not EMAIL_REGEX.match(email):
        return False, "Format d'email invalide."

    domain = email.split("@")[-1].lower()

    if domain in DISPOSABLE_DOMAINS:
        return False, "Merci d'utiliser votre email professionnel — votre bilan vous sera envoyé à cette adresse."

    return True, ""


# ─── HONEYPOT ─────────────────────────────────────────────────────────────────
def check_honeypot(honeypot_value: str) -> bool:
    """Retourne True si c'est un humain (honeypot vide), False si c'est un bot."""
    return not honeypot_value or honeypot_value.strip() == ""
