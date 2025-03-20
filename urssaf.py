import base64
from collections import Counter
import hashlib
import json
import logging
import random
import re
import requests
import urllib.parse

import jwcrypto.jws
import jwcrypto.jwk
import lxml.html



class AlreadyPaidError(Exception):
    pass

class PaidIncorrectAmountError(Exception):
    pass



def random_string(length):
    charset = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choices(charset, k=length))



def matching_braces(s):
    assert s[0] == "{"
    cnt = 0
    for i, c in enumerate(s):
        if c == "{":
            cnt += 1
        elif c == "}":
            cnt -= 1
            if cnt == 0:
                return s[:i + 1]



def enclosing_opening_brace(s, start):
    if start is None:
        start = len(s) - 1

    pos = start
    cnt = int(s[start] == "{")
    while pos >= 0:
        c = s[pos]
        if c == "}":
            cnt += 1
        elif c == "{":
            if cnt == 0:
                return pos
            cnt -= 1

        pos -= 1

    raise ValueError("No matching opening braces")



class URSSAF(object):
    baseurl = "https://www.autoentrepreneur.urssaf.fr/"
    servicesurl = baseurl + "services/"
    configurl = servicesurl + "assets/config/config.js"



    def __init__(self, login, pwd):
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "Mozzarella Firefox/1337.42 because fuck you! That's why."
        retry_status = {
            403, 408, 413, 429, 499,
            500, 501, 502, 503, 504, 509, 511,
            520, 521, 522, 523, 524, 525, 526, 527
        }
        r = requests.adapters.Retry(total=3, backoff_factor=1, allowed_methods=None, status_forcelist=retry_status)
        a = requests.adapters.HTTPAdapter(max_retries=r)
        self._session.mount("http://", a)
        self._session.mount("https://", a)

        self._config = None
        self._main_config = None
        self._verif = None
        self._access_token = None
        self._profile_ctx = None
        self._mandates = None
        self._state = None
        self._context = None

        self._login(login, pwd)

    def request(self, method, url, *args, **kwargs):
        res = self._session.request(method, url, *args, **kwargs)
        res.raise_for_status()
        return res

    def request_auth(self, method, url, *args, **kwargs):
        if self._access_token is None:
            raise RuntimeError("Must be logged in before using request_auth method")

        headers = {"Authorization": "Bearer " + self._access_token}
        headers.update(kwargs.pop("headers", {}))
        res = self.request(method, url, *args, headers=headers, **kwargs)
        return res

    def get(self, url, *args, **kwargs):
        res = self._session.get(url, *args, **kwargs)
        res.raise_for_status()
        return res

    def post(self, url, *args, **kwargs):
        res = self._session.post(url, *args, **kwargs)
        res.raise_for_status()
        return res

    def get_html(self, url, *args, **kwargs):
        res = self.get(url, *args, **kwargs)
        doc = lxml.html.fromstring(res.content, base_url=res.url)
        doc.make_links_absolute()
        return doc

    def get_auth(self, url, *args, **kwargs):
        return self.request_auth("GET", url, *args, **kwargs)

    def post_xhr_json(self, url, *args, **kwargs):
        res = self.request_auth("POST", url, *args, **kwargs)
        return res.json()



    def _get_config(self):
        if self._config is not None:
            return self._config

        res = self.get(self.configurl)
        j = res.text[res.text.find("{"):].replace(",\n}", "\n}")
        self._config, _ = json.JSONDecoder().raw_decode(j)
        return self._config



    def _get_mainjs(self):
        doc = self.get_html(self.servicesurl)
        mainscripts = doc.cssselect('script[src*="/main."][src$=".js"]')
        if len(mainscripts) == 0:
            raise ValueError("No main.js found")
        if len(mainscripts) > 1:
            raise ValueError("Several main.js found")

        mainsjsurl = mainscripts[0].get("src")

        # TODO: Cache the 1.7MB main.js
        res = self.get(mainsjsurl)
        return res.text



    def _get_main_config(self):
        if self._main_config is not None:
            return self._main_config

        config = self._get_config()
        mainjs = self._get_mainjs()
        oauthidx = mainjs.index("oauth:")
        cfgidx = enclosing_opening_brace(mainjs, oauthidx)
        oauthcfg = matching_braces(mainjs[cfgidx:])
        oauthcfg = re.sub(r'(?<=[{,])(\w*):', '"\\1":', oauthcfg)

        replace = {
            "!0": "true",
            "!1": "false",
        }

        cnt = Counter()
        for k in config:
            c = Counter(re.findall(r'\b(\w+)\.' + re.escape(k), oauthcfg))
            cnt.update(c)
        (config_varname, _) = cnt.most_common(1)[0]
        replace.update({config_varname + "." + k: f'"{v}"' for k, v in config.items()})

        # Get the shorthand variables as well
        varsidx = mainjs.rindex(";", 0, cfgidx) + 1
        shortvars = dict(re.findall(r'(\w+)\s*=\s*([\w.]+),', mainjs[varsidx:cfgidx]))
        del shortvars[config_varname]

        # ... and the special base URL variable
        baseurlvar, = re.findall(r'(\w+)\s*=\s*[^,]*\blocation\b[^,]*,', mainjs[varsidx:cfgidx])
        shortvars[baseurlvar] = json.dumps(self.servicesurl)
        replace.update({k: replace.get(v, v) for k, v in shortvars.items()})

        search = r'|'.join(r'(?<!\w)' + re.escape(s) + r'(?!\w)' for s in replace.keys())
        oauthcfg = re.sub(search, lambda m: replace[m.group(0)], oauthcfg)

        # Evaluate the parseInt calls
        parseInt = lambda m: str(int(*eval(m.group(1))))
        oauthcfg = re.sub(r'parseInt\(([\d\s,"\']*)\)', parseInt, oauthcfg)

        # Concat the strings
        oauthcfg = oauthcfg.replace('"+"', '')

        # Evaluate the ternary operators
        string = r'"(?:[^\\"]|\\")*"' + r'|' + r"'(?:[^\\']|\\')*'"
        nonstring = r'[^"\']'
        elem = r'\w+|' + string
        tokens = r'(?:%s)*?' % elem
        prefix = r'(?:%s|%s)*?' % (nonstring, string)
        r = r'(%s)(%s)\?(%s):(%s)' % (prefix, elem, tokens, elem)

        while True:
            m = re.match(r, oauthcfg)
            if m is None:
                break
            before, cond, iftrue, iffalse = m.groups()
            repl = before + (iftrue if eval(cond) else iffalse)
            oauthcfg = oauthcfg[:m.start()] + repl + oauthcfg[m.end():]

        self._main_config, _ = json.JSONDecoder().raw_decode(oauthcfg)
        return self._main_config



    def _get_profile_context(self):
        if self._profile_ctx is None:
            cfg = self._get_main_config()
            url = cfg["profil"]["baseURL"] + "/contexte"
            self._profile_ctx = self.post_xhr_json(url, json={})
        return self._profile_ctx



    def _login_url_params(self, oauthcfg):
        res = {
            "response_type": oauthcfg["responseType"],
            "client_id": oauthcfg["clientId"],
            "state": random_string(43),
            "redirect_uri": oauthcfg["redirectUri"],
            "scope": oauthcfg["scope"],
            "nonce": random_string(43),
        }

        if res["response_type"] == "code":
            self._verif = random_string(43).encode()
            chall = hashlib.sha256(self._verif).digest()
            chall = base64.urlsafe_b64encode(chall).replace(b"=", b"")
            res["code_challenge"] = chall
            res["code_challenge_method"] = "S256"

        return res



    def _verify_token(self, access_token):
        config = self._get_config()
        jwksurl = config["ARCHIMED_LOGIN_API_URL"] + "jwks"
        jwks = self.get(jwksurl).content
        keys = jwcrypto.jwk.JWKSet.from_json(jwks)

        signer = jwcrypto.jws.JWS()
        signer.deserialize(access_token)
        signer.verify(keys.get_key(signer.jose_header["kid"]))
        if not signer.is_valid:
            raise ValueError("Invalid access token signature")



    def _login(self, login, pwd):
        maincfg = self._get_main_config()
        oauthcfg = maincfg["oauth"]

        loginparams = self._login_url_params(oauthcfg)
        doc = self.get_html(oauthcfg["loginUrl"], params=loginparams)

        # Fix requestOrigin argument that's not sufficiently encoded (see mirev24.js)
        url = urllib.parse.urlparse(doc.base_url)
        qs = urllib.parse.parse_qs(url.query)
        reqorig = qs["requestOrigin"][0]
        m = re.search(r'&redirect_uri=(.*?)&END=TRUE', reqorig)
        if m is not None:
            redir = urllib.parse.quote(m.group(1), safe="")
            reqorig = reqorig.replace(m.group(0), '&redirect_uri=' + redir)

        reqorig = urllib.parse.quote(reqorig, safe="")

        # Fill and submit the actual form
        forms = doc.cssselect("form#identification")
        if len(forms) == 0:
            raise ValueError("Can't find identification form")
        if len(forms) > 1:
            raise ValueError("Several identification forms")

        formvalues = {
            "username": login,
            "password": pwd,
            "requestOrigin": reqorig
        }
        form = forms[0]
        res = self.request(form.method, form.action, json=formvalues).json()
        if res["status"] != 302:
            raise ValueError("Can't authenticate:" + json.dumps(res))

        url = urllib.parse.urlparse(res["redirect"])
        if loginparams["response_type"] == "code":
            qs = urllib.parse.parse_qs(url.query)
            data = {
                "grant_type": "authorization_code",
                "code": qs["code"][0],
                "redirect_uri": oauthcfg["redirectUri"],
                "code_verifier": self._verif,
                "client_id": oauthcfg["clientId"],
            }
            res = self.post(oauthcfg["tokenEndpoint"], data=data).json()
            self._verify_token(res["id_token"])
            self._access_token = res["access_token"]

        elif loginparams["response_type"] == "token":
            qs = urllib.parse.parse_qs(url.fragment)
            self._verify_token(qs["id_token"][0])
            self._access_token = qs["access_token"][0]
        else:
            raise NotImplementedError(f"OAuth authentication flow {loginparams['response_type']!r} not supported")



    def get_mandates(self):
        if self._mandates is not None:
            return self._mandates

        cfg = self._get_main_config()
        url = cfg["mandat"]["baseURL"] + "/mandat/lister"
        mandates = self.post_xhr_json(url, json=self._get_profile_context())

        # Convert the JSON objects to something more useful
        self._mandates = []
        for m in mandates["contexte"]["mandats"]:
            m = {
                "bank_name": m["banque_lib"],
                "SIRET": m["debiteur_siret"],
                "BIC": m["debiteur_bic"],
                "IBAN": m["debiteur_iban"],
                "ICS": m["ICS"],
                "RUM": m["RUM"],
                "creditor": {
                    "name": m["creancier_lib"],
                    "org": m["creancier_orga"],
                    "ICS": m["creancier_ics"]
                }
            }
            self._mandates.append(m)

        return self._mandates



    def get_context(self):
        if self._context is not None:
            return self._context

        # TODO extract url fragments from main.*.js
        cfg = self._get_main_config()
        url = cfg["declaration"]["baseURL"] + "/declaration/contexte"
        ctx = self.post_xhr_json(url, json=self._get_profile_context())

        mode = ctx["contexte"]["mode"]
        # TODO: allow resuming a previously aborted declaration
        if mode not in ("nouvelle", "existante"):
            logging.warning("Unknown context mode %r, won't declare anything with this", mode)
            self._state = "mode_error"

        self._context = ctx
        return ctx



    def post_declaration_context(self, urlfrag, *args, **kwargs):
        cfg = self._get_main_config()
        url = cfg["declaration"]["baseURL"] + urlfrag
        res = self.post_xhr_json(url, *args, json=self._context, **kwargs)
        self._context = res
        return res



    def declare(self, amount, redo="never"):
        if redo not in ("never", "ifchanged", "always"):
            raise ValueError(f"Unknown argument value for redo={redo!r}")

        if self._state is not None:
            if "error" in self._state:
                raise RuntimeError("Can't declare anything while in state %r" % self._state)
            logging.warning("Restarting declaration from state %s", self._state)

        amount = str(round(amount))
        ctx = self.get_context()

        declexpected = (len(ctx["data"]["declaration"]["certif"]) <= 2)
        paymentexpected = (ctx["data"]["paiement"]["attendu"] == "true")
        decl_done = not (declexpected or paymentexpected)

        if decl_done and redo == "always":
            logging.info("Declaration already done. Redoing it as requested.")

        elif decl_done and redo != "always":
            declared_prev = ctx["data"]["declaration"]["ass"]["ass_autres"]
            if declared_prev == amount:
                logging.debug("Declaration already done. Not redoing.")
                raise AlreadyPaidError("Already declared and paid the right amount. Not redoing anything.")
            else:
                logging.info("Declared %d instead of %d.", declared_prev, amount)
                if redo == "never":
                    logging.debug("Ignoring declaration discrepancy.")
                    raise PaidIncorrectAmountError("Declared %s instead of %s, should redo the declaration." % (declared_prev, amount))

                assert redo == "ifchanged" # No other possibility
                logging.info("Redoing declaration.")


        ctx["data"]["declaration"]["ass"]["ass_autres"] = amount

        # submit form
        logging.info("Declaring %s euros", amount)
        self.post_declaration_context("/declaration/calculer")

        # Extract interesting informations
        ret = []
        for tax in self._context["data"]["declaration"]["cts"]:
            t = {
                "desc": tax["lib"],
                "amount": float(tax["mt"]),
                "rate": float(tax["taux"][:-1])
            }
            ret.append(t)

        self._state = "declared"
        return ret, float(self._context["data"]["declaration"]["mts"]["mtapa"])



    def validate_declaration(self):
        if self._state != "declared":
            raise RuntimeError("Must delcare an income before validating it")

        amount = self._context["data"]["declaration"]["ass"]["ass_autres"]
        logging.info("Validating declaration for %s euros", amount)

        self._context["data"]["declaration"]["certif"] = None
        self.post_declaration_context("/declaration/valider")
        self._state = "validated"



    def pay(self, mandate=None):
        # TODO: Maybe allow to customize the amount paid from each mandate?

        if self._state != "validated":
            raise RuntimeError("Must delcare and validate an income before paying it")

        if mandate is None:
            mandates = self.get_mandates()
            if len(mandates) == 0:
                raise RuntimeError("No registered mandate to pay with")

            mandate = mandates[0]

        rum = mandate["RUM"]

        # Find the mandate to fill
        # Why the fuck did they use Attr_0, Attr_1, ... instead of a list?
        sepa = self._context["data"]["paiement"]["sepa"]
        rumkey = next(k for k in sepa.keys() if k.startswith("SepaRum_") and sepa[k] == rum)
        mandateidx = rumkey[len("SepaRum_"):]

        # Fill in the form
        amount = self._context["data"]["declaration"]["mts"]["mtapa"]
        sepa["SepaMontant_" + mandateidx] = amount
        sepa["SepaMontantVal_" + mandateidx] = amount
        sepa["SepaTotalMontant"] = amount

        # send the form
        logging.info("Paying %s euros with IBAN %s", amount, mandate["IBAN"])
        self.post_declaration_context("/paiement/sepa")
        self._state = None

        # return the relevant information (link to pdf)
        return self._context, self._context["data"]["declaration_pdf"]
