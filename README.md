![Hustle](doc/_static/hustle.png)

Hustle (beta)
=============

A column oriented, embarrassingly distributed relational NoSQL database.

Features
--------

* column oriented - super fast queries
* distributed insert - Hustle is designed for petabyte scale datasets in a distributed environment with massive write loads
* compressed - bitmap indexes, lz4, and prefix trie compression
* relational - join gigantic datasets
* partitioned - smart shards
* embarrassingly distributed (Disco: http://discoproject.org/)
* embarrassingly fast (lmdb:  http://symas.com/mdb/)
* NoSQL - Python DSL
* bulk append only (it's for 'log' data)
* definitely consistent
* REPL/CLI query interface

BETA / EAP
==========

Please note that this software is beta/early access.  We intend that you thoroughly enjoy wrangling unimaginably large datasets with this software, but really have no idea how it will perform in your particular installation.  Be nice and drop us a GitHub 'issue' or just email me at tspurway@gmail.com for help.

Installation
============

After cloning this repo, here are some considerations:

* you will need Python 2.7 or higher - note that it *probably* won't work on 2.6 (has to do with pickling lambdas...)
* you need to install Disco 0.5 and its dependencies - get that working first
* you need to install Hustle and its 'deps' thusly:

```
cd hustle
sudo ./bootstrap.sh
```

That should do it.  Now, there is a config file in /etc/hustle you should take a look at, and another in /etc/disco.  More on this soon...

Documentation
=============

http://changoinc.github.io/hustle/