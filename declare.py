#!/usr/bin/env python3

import argparse
import configparser
import datetime
import json
import locale
import logging
import logging.config
import os
import subprocess
import sys
import traceback

import mailer
import paymentfile
import urssaf



SELFPATH = os.path.dirname(os.path.realpath(sys.argv[0]))



def logging_getHandler(name):
    for h in logging.getLogger().handlers:
        if h.name == name:
            return h
    return None



def get_payments(payfile, begin, end):
    # Parse Paymentfile
    payments = paymentfile.PaymentFile(payfile)

    # Select the payments of last month
    pay = payments.payments_in_range(begin, end)

    # Sum the amount
    total = sum(p.amount for p in pay)

    msg = "For the period %s to %s " % (begin, end)
    if pay:
        msg += "the following payments have been taken into account:\n"
    else:
        msg += "no payment have been recorded.\n"

    for p in pay:
        msg += "%s\n" % p

    msg += "\nTotal: %d€\n" % total

    return total, msg



def tax_message(taxes, taxes_total, mandate):
    taxes = [t for t in taxes if t["amount"] > 0]
    if len(taxes) == 0:
        msg = "\nNo taxes.\n"
    else:
        msg = "\nTaxes details:\n"

    for t in taxes:
        if t["amount"] > 0:
            msg += "%s (%.2f%%): %d€\n" % (t["desc"], t["rate"], t["amount"])

    if taxes_total > 0:
        msg += "Total taxes to be paid: %d€\n" % taxes_total
        msg += "\nThis amount will be paid from :\n"
        msg += "Bank: %s\n" % mandate["bank_name"]
        msg += "IBAN: %s\n" % mandate["IBAN"]

    return msg



def dostuff(config, mailsender, payfile, pdfdir, redo="never"):
    # Range of dates to consider
    end = datetime.date.today().replace(day=1)
    begin = (end - datetime.timedelta(days=1)).replace(day=1)
    total, msg = get_payments(payfile, begin, end)

    pdfname = begin.strftime("CA_%Y_%m.pdf")
    pdfpath = "%s/%s" % (pdfdir, pdfname)

    if os.path.isfile(pdfpath):
        filetype = subprocess.check_output(["file", "-b", "--mime-type", pdfpath])
        filetype = filetype.decode().rstrip()
        if not filetype.startswith("application/pdf"):
            logging.warning("File %r does not look like a PDF. Has mime type %r", pdfpath, filetype)
            logging.warning("Redoing declaration from scratch.")
            redo = "always"

    elif redo != "always":
        logging.warning("No PDF summary. Forcing the redeclaration in order to complete it.")
        redo = "always"

    logging.debug("Declaration summary:\n%s", msg)

    # Declare on the URSSAF
    urssafcfg = config["URSSAF"]
    urss = urssaf.URSSAF(urssafcfg["login"], urssafcfg["password"])

    if len(urss.get_mandates()) == 0:
        raise RuntimeError("No registered mandate to pay with. Use the website for this.")

    # TODO: Maybe allow to choose which mandate to pay from?
    mandate = urss.get_mandates()[0]

    taxes, taxes_total = urss.declare(total, redo)
    msg += tax_message(taxes, taxes_total, mandate)
    logging.debug("Message to be send by e-mail:\n%s", msg)

    urss.validate_declaration()
    ctx, pdfurl = urss.pay(mandate)

    # We need to be authenticated and send the 'Authorization' header to download the PDF
    pdf = urss.get_auth(pdfurl).content

    logging.info("Saving PDF declaration as %r", pdfpath)
    mode = "wb" if redo != "never" else "xb"
    with open(pdfpath, mode) as fp:
        fp.write(pdf)

    ctxjson = json.dumps(ctx, indent=8).encode("utf-8")
    att = [("declaration_context.json", ctxjson), (pdfname, pdf)]

    title = "Declared %d€, paid %d€" % (int(total), int(taxes_total))
    mailsender.message(urssafcfg["email"], title, msg, att)



def main():
    locale.setlocale(locale.LC_ALL, '')
    logging.config.fileConfig(os.path.join(SELFPATH, "logconf.ini"), disable_existing_loggers=False)

    parser = argparse.ArgumentParser(description="Bot de déclaration pour l'URSSAF")
    parser.add_argument("cfgfile", metavar="configfile", help="Fichier de configuration")
    parser.add_argument("--payment", "-p", metavar="file", help="Fichier des factures payées")
    parser.add_argument("--ca-pdf-dir", "-c", metavar="dir", default=".", help="Répertoire où enregistrer le PDF de déclaration du chiffre d'affaire")
    parser.add_argument("--redo-declaration", "--redo", choices=["never", "ifchanged", "always"], nargs="?", const="always", default="never", help="Refait la déclaration si elle existe déjà")
    parser.add_argument("--no-error-mail", action="store_true", help="N'envoie pas de mail pour les erreurs")
    parser.add_argument("--verbose", "-v", action="count", default=0, help="Augmente le niveau de verbosité")
    parser.add_argument("--quiet", "-q", action="count", default=0, help="Diminue le niveau de verbosité")

    args = parser.parse_args()

    configpath = args.cfgfile
    verbose = args.verbose - args.quiet
    payfile = args.payment
    capdfdir = args.ca_pdf_dir
    redo = args.redo_declaration
    errormail = not args.no_error_mail

    loglevels = ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"]
    ch = logging_getHandler("consoleHandler")
    curlevel = logging.getLevelName(ch.level)
    curlevel = loglevels.index(curlevel)
    verbose = min(len(loglevels) - 1, max(0, curlevel + verbose))
    ch.setLevel(loglevels[verbose])

    logging.info("Reading config file %s", configpath)
    config = configparser.ConfigParser()
    config.read(configpath)

    smtphost = config["SMTP"]["smtphost"]
    smtpport = config["SMTP"].get("smtpport")
    smtpauth = config["SMTP"].get("smtpauthmethod")
    smtpuser = config["SMTP"].get("smtpuser")
    smtppassword = config["SMTP"].get("smtppwd")
    smtpoauthcmd = config["SMTP"].get("smtpoauthtokencmd")

    mailsender = mailer.Mailer(smtphost, smtpport, smtpauth,
                               smtpuser, smtppassword, smtpoauthcmd)

    try:
        dostuff(config, mailsender, payfile, capdfdir, redo)
    except KeyboardInterrupt:
        pass
    except urssaf.AlreadyPaidError:
            logging.info("Already declared with correct amount. Ignoring.")
    except:
        logging.exception("Top-level exception:")
        if not errormail:
            raise

        msg = "Exception caught while trying to run the declaration.\n\n"
        msg += traceback.format_exc()
        mailsender.error(smtpuser, msg)



if __name__ == '__main__':
    main()
