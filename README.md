# Programmes URSSAF

Il y a deux programmes dans ce repository. Le premier effectue le rapprochement
bancaire afin de déterminer le chiffre d'affaire réalisé. Le deuxième est chargé
de déclarer ce chiffre d'affaire automatiquement sur le site de l'URSSAF et paye
avec un mandat enregistré sur le site.


## Rapprochement bancaire

Le programme `checkpayment.py` lit un résumé des factures émises, et se connecte
au compte bancaire. Il met en correspondance les transactions du compte bancaire
et les factures émises. Cette correspondance est ensuite écrite dans
*paymentfile* et envoyé par mail.

### Utilisation

L'utilisation prévue est de lancer ce programme régulièrement afin de générer le
*paymentfile*. Le matching entre les factures et les transactions bancaires
étant approximatif, il est important de vérifier manuellement que la
correspondance est correcte et de corriger le *paymentfile* si nécessaire. Afin
de ne pas oublier, le programme envoie un mail automatiquement lorsqu'une
facture a été matchée avec une transaction.

Le programme peut être lancé par une entrée cron comme suit :

    0 8 */5 * * dir/to/checkpayment.py dir/to/urssaf.ini --invoice-dir path/to/invoices/dir --payment dir/to/paymentfile.txt

Le premier argument est le chemin vers le fichier de configuration dont la
syntaxe est détaillée plus loin.

L'option `--invoice-dir` indique le chemin du répertoire contenant les fichiers
`.inv` qui résument les factures. Tous les fichiers du répertoire dont le nom se
termine par `.inv` seront lus. Leur syntaxe est détaillée plus loin.

L'option `--payment` indique le chemin vers le *paymentfile*. Ce fichier
contient le matching entre les factures et les transactions.

### Matching facture - transaction
Chaque facture est définie par 4 attributs :
- le numéro de facture ;
- le montant de la facture ;
- la date d'établissement de la facture ;
- la date limite de paiement.

Chaque transaction est définie par 3 attributs :
- la date de la transaction ;
- le montant ;
- le libellé.

Pour mettre en correspondance les factures et transactions, il faut que le
montant corresponde exactement et que la date de la transaction se trouve entre
la date d'établissement de la facture et la date limite de paiement. En cas
d'ambiguïté, les plus anciennes transactions sont matchées avec les plus
anciennes factures. Les factures payées hors-délais (plus quelques jours) ne
seront pas matchées et devront être ajoutées manuellement au fichier.

Le *paymentfile* est aussi utilisé pour ignorer les factures et les transactions
déjà mises en correspondances avec une transaction ou facture réciproquement.
Pour reconnaître les transactions déjà matchées avec une facture, la date,
montant et libellé sont utilisés.

Note: Certaines banques peuvent modifier des transactions à postériori,
notamment le libellé. Cela peut théoriquement provoquer la mises en
correspondance d'une même transaction avec plusieurs factures. Il ne semble pas
y avoir de manière fiable d'identifier les transactions de manière unique, ni de
déterminer si une transaction est *"finalisée"*.


## Déclaration et paiement

Le programme `declare.py` lit le fichier de paiement des factures, déclare
auprès de l'URSSAF les paiements reçus durant la période actuellement à
déclarer. Il paye également avec l'un des mandats de prélèvement enregistrés sur
le site de l'URSSAF.

Le programme n'offre actuellement pas le choix du mandat à utiliser pour payer
ni la possibilité d'enregistrer un nouveau mandat. Ces actions doivent être
effectées depuis le site web de l'URSSAF. Il ne gère pas non plus la période
initiale où les déclarations doivent être décalées de 90 jours après le début de
l'activité.

### Utilisation

L'utilisation prévue est de lancer ce programme régulièrement avec par exemple
une entrée cron comme suit :

    0 8 10 * * $HOME/code/urssaf_declare/declare.py path/to/urssaf.ini --payment path/to/paymentfile.txt --ca-pdf-dir dir/for/pdfs

Le premier argument est le chemin vers le même fichier de configuration que le
programme de rapprochement bancaire. Sa syntaxe est détaillée plus loin.

L'option `--payment` est identique au programme de rapprochement bancaire et
sert à indiquer le chemin vers le *paymentfile*. Ce fichier contient le matching
entre les factures et les transactions. Il est utilisé en lecture seule par ce
programme.

L'option `--ca-pdf-dir` indique le répertoire où sera stocké le PDF justifiant
de la déclaration et du paiement de cotisations. Notez que si le fichier existe
déjà, son écriture sera abandonnée. Le fichier est également envoyé par mail à
l'adresse indiquée dans la configuration.

D'autres options sont disponibles, notamment `--already-paid-noop` afin de ne
rien faire et ne pas émettre d'erreur si la déclaration et le paiement ont déjà
été effectués. Ceci peut servir à relancer automatiquement le programme
plusieurs fois par mois au cas où la première exécution aurait planté, notamment
à cause du site de l'URSSAF.


# Fichier de configuration

Ces programmes utilisent le module python `configparser` pour parser le fichier
de configuration. Sa documentation donnera les détails de la syntaxe si besoin.
<https://docs.python.org/3/library/configparser.html>

Globalement fichier de configuration suit la syntaxe des fichiers INI et
ressemble à ceci.

```ini
[SMTP]
smtphost = smtp.gmail.com
smtpport = 465
smtpauth = login
smtpuser = youraccount@gmail.com
smtppwd = GMa1lP4s5W0rD
#smtpoauthtokencmd = ~/some/command/that/outputs/oauth/token

[Bank]
woobbackend = cragr
woobbackendargs = {"website": "www.ca-tourainepoitou.fr"}
login = Login
password = 123456
accountno = 12345678001
email = something@example.com

[URSSAF]
login = 123456789012345
password = P4ssw0rd
email = something@example.com
```

La section `SMTP` décrit le serveur SMTP à utiliser pour envoyer des mails.
Ensuite, la section `Bank` définit l'accès au compte bancaire.

## Section `[SMTP]`
- `smtphost` et `smtpport` définissent le nom de domaine et le port du serveur
  SMTP. Note: Il s'agit nécessairement du port SMTPS et le port par défaut est
  465.
- `smtpauth` définit la méthode d'authentification à utiliser pour ce serveur.
Les deux méthodes disponibles sont `login` et `oauth`.
- `smtpuser` définit le login nécessaire pour se connecter au serveur SMTP. Il
est utilisé aussi bien pour la méthode `login` que pour la méthode `oauth`.
- `smtppwd` définit le mot de passe nécessaire pour se connecter avec la
méthode `login`.
- `smtpoauthtokencmd` définit la commande externe à exécuter pour récupérer un
*access token* pour se connecter avec la méthode `XOAUTH2` au serveur SMTP.
Cette commande est exécutée dans un shell et ne doit afficher que le token sur
sa sortie standard.

Si `smtpauth` est omit, il est deviné à partir de l'existence de `smtppwd` et
`smtpoauthtokencmd`. Si rien n'est donné, aucune authentification n'est tenté.

## Section `[Bank]`
- `woobbackend` définit le nom du backend Woob à utiliser. Par exemple,
  `crgr` pour le crédit agricole. D'autres backends sont supportés pour d'autres
  banques. La liste est accessible sur le site de Woob.
  <https://woob.tech/modules>
- `woobbackendargs` est un json définissant les arguments supplémentaires
  nécessaires lors de l'initialisation du backend en plus du login et du mot de
  passe. Cette information ne semble pas être bien documentée. Il faudra
  probablement lire le code source de Woob.
- `login` et `password` définissent le login et le mot de passe à utiliser pour
  se connecter sur le site de la banque.
- `accountno` donne le numéro du compte dont les transactions seront mises en
  correspondance avec les factures.
- `email` définit l'adresse mail où envoyer le résumé du rapprochement bancaire
  s'il a trouvé un nouveau matching.

## Section `[URSSAF]`
- `login` et `password` définissent le login et le mot de passe à utiliser pour
  se loguer sur le site de l'URSSAF.
- `email` définit l'adresse mail où envoyer le résumé de la déclaration et du
  paiement. Ce mail sera accompagné d'un fichier JSON tel que renvoyé par le
  serveur lors de la validation du paiement et du PDF jusitifiant de la
  déclaration.

# Fichiers `inv` (résumés de factures)
Chaque fichier `.inv` contient les informations résumées d'une facture donnée.
Ils sont lus par le programme de rapprochement bancaire. Ils sont conçus pour
être générés automatiquement. Par un template de factures LaTeX par exemple. :)
Mais peuvent aussi être écrits à la main.

## Format
Chaque ligne commence par un mot-clé et est suivi d'une espace et d'une valeur.
Par exemple :
```
invoicenumber 0001
invoicedate 25/11/2020
deadline 25/12/2020
amount 1234.56
```

- `invoicenumber` donne le numéro de la facture. Il n'a pas besoin d'être
numérique, n'importe quelle chaîne de caractère unique est suffisante.
- `invoicedate` et `deadline` donne la date de facturation et la date limite de
paiement. Elles doivent être données sous la forme `DD/MM/YYYY`.
- `amount` donne le montant de la facture. Le montant peut contenir un nombre
arbitraire de chiffres après la virgule, le montant sera arrondi à deux chiffres
après la virgule.

Dans le future, la ligne `amount` sera peut-être séparée en plusieurs lignes
afin de permettre de déclarer à l'URSSAF des prestations de services, des
bénéfices commerciaux et non-commerciaux.

# Paymentfile
Le *paymentfile* liste les factures payées avec la transaction associée. Il est
écrit par le programme de rapprochement bancaire. Il est lu par le programme de
déclaration de chiffre d'affaire et est aussi lu par le programme de
rapprochement bancaire afin de ne pas matcher deux fois une même facture ou une
même transaction.

## Format
Le fichier contient sur chaque ligne les informations identifiant une facture et
une transaction. En voici un exemple.

```
# Date facture montant libellé
2020-11-16 0001 1000.00 VIREMENT EN VOTRE FAVEUR AWESOME COMPANY # Bad ass stunt
2020-12-04 0002 500.00 VIREMENT EN VOTRE FAVEUR AWESOME COMPANY

2020-12-29 0003 700.00 VIREMENT EN VOTRE FAVEUR LIKE A BOSS # Broke my leg like a boss
```

Le fichier peut contenir des commentaires, des lignes vides ou des lignes de
données. Les lignes de données sont constituées de 4 colonnes, date, numéro de
facture, montant et libellé de transaction. Ces 4 colonnes sont séparées par des
caractères blancs (espaces ou tabulations).

### Commentaires
Tout ce qui se trouve après un symbole `#` est ignoré. Ceci peut être utilisé
pour ajouter des commentaires dans le fichier. Soit toute la ligne, soit en fin
de ligne.

### Date
La date est au format `YYYY-MM-DD`. Elle indique la date du paiement. Elle est
utilisée pour retrouver la transaction afin de le pas l'associer à deux
factures. Elle est aussi utilisée pour déclarer le montant du chiffre d'affaire
à l'URSSAF.

### Numéro de facture
Le numéro de facture sert à retrouver quelle facture a été payée afin de ne pas
chercher à nouveau la transaction qui lui corresponde.

### Montant
Le montant de la transaction bancaire. Il est utilisé pour identifier la
transaction afin de ne pas l'associer à une deuxième facture. Il sera en général
identique au montant de la facture. Il est possible de mettre en correspondance
manuellement une transaction avec une facture d'un montant différent. Ceci peut
être utile si une facture a été payée en plusieurs transactions par exemple.

### Libellé
Libellé de la transaction bancaire. Il est utilisé pour identifier la
transaction.

# Améliorations possibles

- Tester avec d'autres banques.
- Harmoniser le format des dates entre les fichiers `inv` et le *paymentfile*.
- Séparer le montant des factures selon les cases à remplir sur le site de
  l'URSSAF.

Contributions welcome.
