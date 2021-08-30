import datetime
import decimal
import logging
import re



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
            raise ValueError("Ill-formatted line in paymentfile: %r" % string)

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

    def payments_in_range(self, begin, end):
        return [p for p in self._payments if p.date >= begin and p.date < end]
