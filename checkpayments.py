#!/usr/bin/env python3

import argparse
import configparser
import datetime
import decimal
import glob
import itertools
import json
import locale
import logging
import re
import traceback

import mailer
import weboob.core



class Invoice(object):
    def __init__(self, invnum, invdate, duedate, amount):
        self.invnum = invnum
        self.invdate = datetime.datetime.strptime(invdate, "%d/%m/%Y").date()
        self.duedate = datetime.datetime.strptime(duedate, "%d/%m/%Y").date()
        self.amount = round(decimal.Decimal(amount), 2)

    @classmethod
    def fromfile(cls, filename):
        keys = ["invoicenumber", "invoicedate", "deadline", "amount"]
        data = {}

        with open(filename) as fp:
            for line in fp:
                line = line.split("#")[0].rstrip()
                if not line:
                    logging.debug("Ignoring empty line")
                    continue

                k, v = line.split(" ", 1)
                data[k] = v

        missing = set(keys) - set(data)
        if missing:
            raise ValueError("Invoice file %s lacks fields %r" % (filename, list(missing)))

        extra = set(data) - set(keys)
        if extra:
            logging.warning("Invoice file %s has extra fields %r", filename, list(extra))

        logging.debug("Read invoice %r", data)
        return cls(*[data[k] for k in keys])

    def __str__(self):
        d1 = self.invdate.strftime("%d/%m/%Y")
        d2 = self.duedate.strftime("%d/%m/%Y")
        return 'Invoice("%s", %s, %s, %s)' % (self.invnum, d1, d2, self.amount)

    def __repr__(self):
        return "<" + str(self) + ">"



class Payment(object):
    rparse = re.compile(r'^(\S+)\s+(\S+)\s+(\S+)\s+(.*)')

    def __init__(self, date, invnum, amount, label):
        self.date = date
        self.invnum = invnum
        self.amount = round(amount, 2)
        self.label = label.rstrip()

    @classmethod
    def from_string(cls, string):
        match = cls.rparse.match(string)
        if match is None:
            raise ValueError("Ill-formatted ligne in paymentfile: %r" % string)

        date, invnum, amount, label = match.groups()
        date = datetime.date.fromisoformat(date)
        amount = decimal.Decimal(amount)
        return cls(date, invnum, amount, label)

    @classmethod
    def from_invoice_transaction(cls, inv, t):
        return cls(t.date, inv.invnum, t.amount, t.label)

    def __str__(self):
        return "%s %s %s %s" % (self.date, self.invnum, self.amount, self.label)



class PaymentFile(object):
    def __init__(self, path=None):
        self._path = path
        self._payments = []

        if path is None:
            logging.debug("No payment file specified")
        else:
            self._read()

    def _read(self):
        try:
            fp = open(self._path)
        except FileNotFoundError:
            logging.warning("Payment file %s doesn't exist yet", self._path)
            return

        with fp:
            for l in fp:
                logging.debug("Reading paymentfile line: %r", l)
                l = l.split("#", 1)[0].rstrip()
                if not l:
                    logging.debug("Ignoring empty line")
                    continue

                p = Payment.from_string(l)
                logging.debug("Read payment: %s", p)
                self._payments.append(p)

            self._payments.sort(key=lambda p: p.date)

    def filter_invoices(self, invoices):
        invoicesdict = {inv.invnum: inv for inv in invoices}
        for p in self._payments:
            if p.invnum in invoicesdict:
                logging.debug("Filtering out already paid invoice: %s", invoicesdict[p.invnum])
                del invoicesdict[p.invnum]

        return list(invoicesdict.values())

    def filter_transactions(self, trans):
        pt = set((p.date, p.amount, p.label) for p in self._payments)
        res = []
        for t in trans:
            if (t.date, t.amount, t.label.rstrip()) not in pt:
                res.append(t)
            else:
                logging.debug("Filtering out transaction already matched: %s", t)

        return res

    def add_payment(self, inv, t):
        p = Payment.from_invoice_transaction(inv, t)
        logging.debug("Adding payment %s", p)
        self._payments.append(p)
        if self._path is None:
            logging.debug("No file to write payment")
            return

        with open(self._path, "a") as fp:
            logging.info("Adding to file %r payment %s", self._path, p)
            print(p, file=fp)



def read_invoices(invdir):
    invlist = []
    for f in glob.iglob(invdir + "/*.inv"):
        invlist.append(Invoice.fromfile(f))

    invlist.sort(key=lambda inv: inv.invnum)
    return invlist



def bank_transactions(cfg, since=None):
    class SilentProgress(weboob.core.repositories.PrintProgress):
        def progress(self, percent, message):
            pass

    boob = weboob.core.Weboob()
    boob.update(SilentProgress())
    args = json.loads(cfg["weboobbackendargs"])
    args.update({"login": cfg["login"], "password": cfg["password"]})
    bank = boob.load_backend(cfg["weboobbackend"], None, args)
    account = bank.get_account(cfg["accountno"])

    trans = bank.iter_history(account)
    if since is not None:
        trans = list(itertools.takewhile(lambda x: x.date >= since, trans))
    return trans



def match_transactions(invoices, trans):
    trans.sort(key=lambda t: t.date)
    matched = []
    for t in trans:
        for inv in invoices:
            if t.amount == inv.amount and t.date >= inv.invdate and t.date <= inv.duedate:
                matched.append((inv, t))
                invoices.remove(inv)
                break

        if len(invoices) == 0:
            break

    return matched, invoices



def dostuff(config, mailsender, invdir, payfile):
    invoices = read_invoices(invdir)

    # Read the paymentfile
    payments = PaymentFile(payfile)

    # Remove the invoices that are already in the paymentfile
    invoices = payments.filter_invoices(invoices)
    if len(invoices) == 0:
        return

    # Check the bank account for new paid invoices and update the paymentfile
    since = min(inv.invdate for inv in invoices)
    trans = bank_transactions(config["Bank"], since=since)

    # Remove transaction that are already in the paymentfile
    trans = payments.filter_transactions(trans)

    # Match the invoices and transactions
    matched, unmatched = match_transactions(invoices, trans)
    overdue = [inv for inv in unmatched if inv.duedate < datetime.date.today()]

    # Append matching in paymentfile
    if len(matched) > 0 and not payfile:
        logging.warning("No payment file to record matched invoices and transactions")

    for inv, t in matched:
        payments.add_payment(inv, t)

    # Send a mail
    msg = ""
    if len(matched) > 0:
        msg += "The following invoices and bank transactions have been matched:\n"
        for inv, t in matched:
            msg += "Invoice %s: %s€ " % (inv.invnum, inv.amount)
            msg += "to be paid between %s and %s\n" % (inv.invdate, inv.duedate)
            msg += "Transaction: %s %s€ " % (t.date, t.amount)
            msg += "%s\n" % t.label
            msg += "\n"

    if len(overdue) > 0:
        msg += "The following invoices are overdue:\n"
        for inv in overdue:
            msg += "Invoice %s for %s€ " % (inv.invnum, inv.amount)
            msg += "to be paid between %s and %s\n" % (inv.invdate, inv.duedate)

    mailsender.message(config["Bank"]["email"], "Invoice matching", msg)



def main():
    locale.setlocale(locale.LC_ALL, '')
    logfmt = "%(asctime)s %(levelname)s: %(message)s"
    logging.basicConfig(format=logfmt, level=logging.WARNING)

    parser = argparse.ArgumentParser(description="Programme de rapprochement bancaire")
    parser.add_argument("cfgfile", metavar="configfile", help="Fichier de configuration")
    parser.add_argument("--invoice-dir", "-i", metavar="dir", help="Répertoire contenant les fichiers .inv")
    parser.add_argument("--payment", "-p", metavar="file", help="Fichier des factures payées")
    parser.add_argument("--no-error-mail", action="store_true", help="N'envoie pas de mail pour les erreurs")
    parser.add_argument("--verbose", "-v", action="count", help="Augmente le niveau de verbosité")

    args = parser.parse_args()

    configpath = args.cfgfile
    verbose = args.verbose
    invdir = args.invoice_dir
    payfile = args.payment
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
        dostuff(config, mailsender, invdir, payfile)
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
