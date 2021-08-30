#!/usr/bin/env python3

import argparse
import configparser
import datetime
import json
import locale
import logging
import traceback

import mailer
import paymentfile
import urssaf



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



def dostuff(config, mailsender, payfile, pdfdir):
    # Range of dates to consider
    end = datetime.date.today().replace(day=1)
    begin = (end - datetime.timedelta(days=1)).replace(day=1)
    total, msg = get_payments(payfile, begin, end)

    # Declare on the URSSAF
    urssafcfg = config["URSSAF"]
    urss = urssaf.URSSAF(urssafcfg["login"], urssafcfg["password"])

    if len(urss.get_mandates()) == 0:
        raise RuntimeError("No registered mandate to pay with. Use the website for this.")

    # TODO: Maybe allow to choose which mandate to pay from?
    mandate = urss.get_mandates()[0]

    taxes, taxes_total = urss.declare(total)
    msg += tax_message(taxes, taxes_total, mandate)
    logging.debug("Message to be send by e-mail:\n%s", msg)

    urss.validate_declaration()
    ctx, pdfurl = urss.pay(mandate)

    # We don't need to use the same session to download the PDF, but it doesn't hurt
    pdf = urss.get(pdfurl).content
    pdfname = begin.strftime("CA_%Y_%m.pdf")
    pdfpath = "%s/%s" % (pdfdir, pdfname)

    logging.info("Saving PDF declaration as %r", pdfpath)
    with open(pdfpath, "xb") as fp:
        fp.write(pdf)

    ctxjson = json.dumps(ctx, indent=8).encode("utf-8")
    att = [("declaration_context.json", ctxjson), (pdfname, pdf)]

    title = "Declared %d€, paid %d€" % (int(total), int(taxes_total))
    mailsender.message(urssafcfg["email"], title, msg, att)



def main():
    locale.setlocale(locale.LC_ALL, '')
    logfmt = "%(asctime)s %(levelname)s: %(message)s"
    logging.basicConfig(format=logfmt, level=logging.WARNING)

    parser = argparse.ArgumentParser(description="Bot de déclaration pour l'URSSAF")
    parser.add_argument("cfgfile", metavar="configfile", help="Fichier de configuration")
    parser.add_argument("--payment", "-p", metavar="file", help="Fichier des factures payées")
    parser.add_argument("--ca-pdf-dir", "-c", metavar="dir", default=".", help="Répertoire où enregistrer le PDF de déclaration du chiffre d'affaire")
    parser.add_argument("--already-paid-noop", action="store_true", help="Ne fait rien si c'est déjà payé")
    parser.add_argument("--no-error-mail", action="store_true", help="N'envoie pas de mail pour les erreurs")
    parser.add_argument("--verbose", "-v", action="count", help="Augmente le niveau de verbosité")

    args = parser.parse_args()

    configpath = args.cfgfile
    verbose = args.verbose
    payfile = args.payment
    capdfdir = args.ca_pdf_dir
    paidnoop = args.already_paid_noop
    errormail = not args.no_error_mail

    if verbose is not None:
        loglevels = ["WARNING", "INFO", "DEBUG", "NOTSET"]
        verbose = min(len(loglevels), verbose) - 1
        logging.getLogger().setLevel(loglevels[verbose])

    logging.info("Reading config file %s", configpath)
    config = configparser.ConfigParser()
    config.read(configpath)

    smtphost = config["SMTP"]["smtphost"]
    smtpport = config["SMTP"].get("smtpport")
    smtpuser = config["SMTP"].get("smtpuser")
    smtppassword = config["SMTP"].get("smtppwd")
    mailsender = mailer.Mailer(smtphost, smtpport, smtpuser, smtppassword)

    try:
        try:
            dostuff(config, mailsender, payfile, capdfdir)
        except urssaf.AlreadyPaidError:
            if paidnoop:
                logging.info("Already declared and paid. Ignoring.")
            else:
                raise

    except KeyboardInterrupt:
        pass
    except:
        logging.exception("Top-level exception:")
        if not errormail:
            raise

        msg = "Exception caught while trying to run the declaration.\n\n"
        msg += traceback.format_exc()
        mailsender.error(smtpuser, msg)



if __name__ == '__main__':
    main()
