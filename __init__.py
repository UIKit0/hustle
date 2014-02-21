"""
:mod:`hustle` -- Hustle Distributed OLAP Query Engine
=====================================================

The :mod:`hustle` module contains everything a client application will
typically need to create hustle :class:`hustle.Table`s and to insert and query
data to/from them.

"""
__version__ = '0.1.1'

import os
import ujson
from hustle.core.marble import Aggregation, Marble, json_decoder, Column, check_query


_TAG_PREFIX = 'hustle:'
_ALG_RIGHT, _ALG_LEFT, _ALG_CENTER = 0x01, 0x10, 0x20


def h_sum(col):
    """
    Return an aggregation for the sum of the given column.  Like SQL sum() function.
    This is used in :func:`hustle.select` calls to specify the sum aggregation over a column in a query::
        select(h_sum(employee.salary), employee.department, where=employee.age > 25)

    returns the total salaries for each departments employees over 25 years old
    """
    return Aggregation("sum", col, lambda a, v: a + v, default=lambda: 0)


def h_count():
    """
    Return an aggregation for the count of each grouped key in a query.  Like SQL count() function::
        select(h_count(), employee.department, where=employee)

    returns a count of the number of employees in each department.
    """
    return Aggregation("count",
                       Column('all', None, type_indicator=1),
                       lambda a, v: a + (v or 1),
                       default=lambda: 0)


def h_max(col):
    """
    Return an aggregation for the maximum of the given column.  Like the SQL max() function::
       select(h_max(employee.salary), employee.department, where=employee)

    returns the highest salary for each department.
    """
    return Aggregation("max", col, lambda a, v: a if a > v else v, default=lambda: -9223372036854775808)


def h_min(col):
    """
    Return an aggregation for the minimum of the given column.  Like the SQL min() function::
        select(h_min(employee.salar), employee.department, where=employee)

    returns the lowest salary in each department.
    """
    return Aggregation("min", col, lambda a, v: a if a < v else v, default=lambda: 9223372036854775807)


def h_avg(col):
    """
    Return an aggregation for the average of the given column.  Like the SQL avg() function::
        select(h_avg(employee.salary), employee.department, where=employee)

    returns the average salary in each department
    """
    return Aggregation("avg", 
                       col, 
                       lambda (a, c), v: (a + v, c + 1), 
                       lambda (a, c): float(a) / c, 
                       default=lambda: (0, 0))


def star(table):
    """
    Return the list of all columns in a table.  This is used much like the '*' notation in SQL::
        select(*star(employee), where=employee.department == 'Finance')

    returns all of the columns from the *employee* table for the Finance department.
    """
    return [table._columns[col] for col in table._field_names]


class Table(Marble):
    """
    The fundamental data type to support Hustle's relational model.  A Table contains a number of named
    :ref:`Fields`, each of which is decorated with schema information.  Note that the table is stored in Disco's
    `DDFS<http://disco.readthedocs.org/en/latest/lib/ddfs.html>`_ distributed file system as a series of replicated
    sub-database files encapsulated by :class:`hustle.core.marble.Marble`.  Each marble contains the actual rows of
    data in a memory-mapped b+ tree implemented using the mighty `LMDB<http://symas.com/mdb/>`_.

    Normally, a table is created using :meth:`hustle.Table.create`, which creates the appropriately named DDFS tag and
    attributes.  To instantiate an existing Table (to use in a query, for example), the :meth:`hustle.Table.from_tag`
    method is used.

    Fields
    ------
    The fields of a Table are it's columns.  Each field has a type, an optional width and an optional index indicator
    as detailed in the following table:

    ======  ====            =====
    Prefix  Type            Notes
    ======  ====            =====
    +       index           create a normal index on this column
    =       index           create a wide index on this column
    @N      unsigned int    N = 1 | 2 | *4 | 8
    #N      signed int      N = 1 | 2 | *4 | 8
    $       string          uncompressed string data
    %N      string          trie compressed N = 2 | 4
    *       string          lz4 compressed
    &       binary          uncompressed blob data
    ======  ======          =====

    fields are specified using the following convention:  *[+|=][type[width]]name*, for example::
        fields=["+$name", "+%2department", "@2salary", "*bio"]

    Indexes
    -------
    By default, columns in Hustle are unindexed.  By indexing a column you make it available for use as a key in
    :ref:`Where Clause` and :ref:`Join Clause` in the :func:`hustle.select` statement.  Unindexed columns can still
    be in the list of selected columns or in aggregation function.  The question whether to index a column or not is a
    consideration of overall memory/disk space used by that column in your database.  An indexed column will take up
    to twice the amount of memory as an unindexed column.

    Wide indexes (the '=' indicator) are used simply as a hint to Hustle to expect the number of unique values for
    the specified column to be very high with respect to the overall number of rows.  The Hustle query optimizer and
    :func:`hustle.insert` function use this information to better manage memory usage when dealing with these columns.

    Integer Data
    ------------

    Integers can be 1, 2, 4 or 8 bytes and are either signed or unsigned.

    String Data and Compression
    ---------------------------

    One of the fundamental design goals of Hustle was to allow for the highest level of compression possible.
    String data is one area that we can maximize compression.  Hustle has a total of five types of string
    representations: uncompressed, lz4 compressed, two flavours of `Prefix Trie<http://en.wikipedia.org/wiki/Trie>`_
    compression, and a binary/blob format.

    The first choice for string compression should be the trie compression.  This offers the best performance and can
    offer dramatic compression ratios for string data that has many duplicates or many shared prefixes (consider the
    strings beginning with "http://www.", for example).  The Hustle trie compression comes in either 2 or 4 byte
    flavours.  The two byte flavour can encode up to 65,536 unique strings, and the 4 byte version can encode over
    4 billion strings.  Pick the two byte flavour for those columns that have a high degree of full-word repetition,
    like 'department', 'sex', 'state', 'country' - whose overall bounds are known.  For strings that have a larger
    range, but still have common prefixes and whose overall length is generally less than 256 bytes, like 'url',
    'last_name', 'city', 'user_agent',

    We investigated many algorithms and implementations of compression algorithms for compressing intermediate sized
    string data, strings that are more than 256 bytes.  We found our implementation of lz4 to be both faster and
    have much higher compression ratios than `Snappy <https://code.google.com/p/snappy/>`_.  Use LZ4 for fields like
    'page_content', 'bio', 'except', 'abstract'.

    Some data doesn't like to be compressed.  UIDs and many other hash based data fields are designed to be evenly
    distributed, and therefore defeat most (all of our) compression schemes.  In this case, it is more efficient to
    simply store the uncompressed string.

    Binary Data
    -----------

    In Hustle, binary data is an attribute that doesn't affect how a string is compressed, but rather, it affects how
    the value is treated in our query pipeline.  Normally, result sets are sorted and grouped to execute
    :ref:`Group By Clause` and :ref:`Distinct Clause` elements of :func:`hustle.select`.  If you have a column that
    contains binary data, such as a .png image or sound file, it doesn't make any sense to sort or group it.

    Partitions
    ----------
    Hustle employs a tecnique for splitting up data into distinct partitions based on a column in the target table.
    This allows us to significantly increase query performance by only considering the data that matches the partition
    specified in the query.  Typically a partition column has the following attributes:
    * the same column is in most Tables
    * the number of unique values for the column is low
    * the column is often in :ref:`Where Clause`s, often as ranges

    The DATE column usually fits the bill for the partition in most LOG type applications.

    Hustle currently supports a single column partition per table.  All partitions must also be indexed.  Partitions
    must currently be uncompressed string types ('$' indicator).

    Partitions are implemented both as regular columns in the database and with a DDFS tagging convention.  All Hustle
    tables have DDFS tags that look like::
        hustle:employees

    where the *name* of the Table is employees.  Tables that have partitions will never actually store data under this
    *root tag* name, rather they will store it under tags that look like::
        hustle:employees:2014-02-21

    this is assuming that the *employee* table has the *date* field as a partition.  All of the data marbles for the
    date 2014-02-22 for the *employees* table is guaranteed to be stored under this DDFS tag.  When Hustle sees a query
    with a where clause identifying this exact date (or a range including this date), we will be able to directly
    and quickly access the coreect data, thereby increasing the speed of the query.

    :type  name: string
    :param name: the name of the table to create

    :type  fields: sequence of string
    :param fields: the list of :ref:`Fields` and their encoded index/type information

    :type  partition: string
    :param partition: the name of the column to act as the partition for this table

    :type  force: bool
    :param force: overwrite the existing DDFS base tag with this schema

    """
    def __init__(self, name, fields=(), partition=None, ):
        super(Table, self).__init__(name, fields, partition)
        self._blobs = None

    @classmethod
    def from_tag(cls, name, **kwargs):
        from hustle.core.settings import Settings
        settings = Settings(**kwargs)
        ddfs = settings['ddfs']

        partition = ujson.loads(ddfs.getattr(cls.base_tag(name), '_partition_'))
        fields = ujson.loads(ddfs.getattr(cls.base_tag(name), '_fields_'))
        return cls(name=name, fields=fields, partition=partition)

    @classmethod
    def create(cls, name, fields=(), partition=None, force=False, **kwargs):
        from hustle.core.settings import Settings
        settings = Settings(**kwargs)
        ddfs = settings['ddfs']

        if ddfs.exists(cls.base_tag(name)):
            print "Table already exists..."
            if force:
                print "   Overwriting schema..."
            else:
                return None

        ddfs.setattr(cls.base_tag(name), '_fields_', ujson.dumps(fields))
        ddfs.setattr(cls.base_tag(name), '_partition_', ujson.dumps(partition))
        return cls(name=name, fields=fields, partition=partition)

    @classmethod
    def base_tag(cls, name, partition=None, extension=''):
        rval = "hustle:" + name
        if extension:
            rval += ':' + extension
        if partition:
            rval += ':' + str(partition)
        return rval


def insert(table, phile=None, streams=None, preprocess=None,
           maxsize=100 * 1024 * 1024, tmpdir='/tmp', decoder=json_decoder,
           lru_size=10000, **kwargs):
    from hustle.core.settings import Settings
    settings = Settings(**kwargs)
    ddfs = settings['ddfs']
    # print 'committed'

    def part_tag(name, partition=None):
        rval = "hustle:" + name
        if partition:
            rval += ':' + str(partition)
        return rval
    if phile:
        streams = [open(phile)]
    lines, partition_files = table._insert(streams, preprocess=preprocess,
                                           maxsize=maxsize, tmpdir=tmpdir,
                                           decoder=decoder, lru_size=lru_size)
    if partition_files is not None:
        for part, pfile in partition_files.iteritems():
            tag = part_tag(table._name, part)
            ddfs.push(tag, [pfile])
            print 'pushed %s, %s to %s' % (part, tag, ddfs)
            os.unlink(pfile)
    return table._name, lines


def dump(result_urls, width=80):
    """
    Dump the results of a query or a table.

    Arguments:
    result_urls - result of a project/aggregate query
    width - the number of columns to constrain output to
    """
    from disco.core import result_iterator
    alignments = None
    for columns, _ in result_iterator(result_urls):
        if not alignments:
            alignments = []
            for column in columns:
                try:
                    float(column)
                    alignments.append(_ALG_RIGHT)
                except:
                    alignments.append(_ALG_LEFT)

        _print_line(columns, width=width, cols=len(alignments),
                   alignments=alignments)


def select(*project, **kwargs):
    """
    Perform a relational query, by selecting rows and columns from one or more tables.

    Arguments:
    project - a positional argument list of columns and aggregate expressions to return in the result
    where - a  list of tables and/or column expressions to restrict the rows of the relations
    join - a list of columns to perform a relational join on
    order_by - a list of columns to order the output by
    distinct - a boolean indicating whether to remove duplicates
    limit - a number or a tuple(offset, total_number) to limit the number of output

    Example:
    # simple projection - note the where expression syntax
    select(
        impressions.timestamp, impressions.userid, impressions.time_on_site,
        where=(impressions.date > '2013-11-01') & (impressions.site == 'metalmusicmachine.com'))

    # simple aggregation - note that a grouping by employee.name is implied
    #   also note that where clauses can have entire tables
    select(
        employee.name, h_sum(employee.salary),
        where=employee,
        order_by=employee.name)

    # aggregation with joins, ordering and limits
    #   - note the where and join clauses must each contain exactly two tables
    select(
        employee.name, department.name, h_sum(employee.salary), h_sum(department.population),
        where=((employee.age > 27) & (employee.sex == 'male'), department),
        join=(employee.department_id, department.id),
        order_by=(department.name, 'sum(salary)'),
        desc=True)"""

    from hustle import _get_blobs
    from hustle.core.settings import Settings
    from hustle.core.pipeline import SelectPipe
    from hustle.core.util import ensure_list

    settings = Settings(**kwargs)
    wheres = ensure_list(settings.pop('where', ()))
    order_by = ensure_list(settings.pop('order_by', ()))
    join = settings.pop('join', ())
    distinct = settings.pop('distinct', False)
    desc = settings.pop('desc', False)
    limit = settings.pop('limit', None)
    ddfs = settings['ddfs']
    autodump = settings['dump']
    partition = settings.get('partition', 0)
    if partition < 0:
        partition = 0
    nest = settings.get('nest', False)
    try:
        check_query(project, join, order_by, limit, wheres)
    except ValueError as e:
        print "  Invalid query:\n    %s" % e
        return None

    name = '-'.join([where._name for where in wheres])[:64]
    job_blobs = set()
    for where in wheres:
        job_blobs.update(tuple(sorted(w)) for w in _get_blobs(where, ddfs))

    job = SelectPipe(settings['server'],
                     wheres=wheres,
                     project=project,
                     order_by=order_by,
                     join=join,
                     distinct=distinct,
                     desc=desc,
                     limit=limit,
                     partition=partition,
                     nest=nest)

    job.run(name='select_from_%s' % name, input=job_blobs, **settings)
    blobs = job.wait()
    if nest:
        rtab = job.get_result_schema(project)
        rtab._blobs = blobs
        return rtab
    elif autodump:
        # the result will be just dumped to stdout
        cols = [c.name for c in project]
        _print_separator(80)
        _print_line(cols, width=80, cols=len(cols),
                   alignments=[_ALG_RIGHT if c.is_numeric else _ALG_LEFT for c in project])
        _print_separator(80)
        dump(blobs, 80)
        return
    else:
        return blobs


def get_tables(**kwargs):
    from hustle.core.settings import Settings
    settings = Settings(**kwargs)
    tags = settings["ddfs"].list(_TAG_PREFIX)
    uniqs = set()
    for tag in tags:
        l = tag.find(':')
        if l > 0:
            ctag = tag[l + 1:]
            r = ctag.find(':')
            if r > 0:
                uniqs.add(ctag[:r])
            else:
                uniqs.add(ctag)

    return sorted(uniqs)


def tables(**kwargs):
    """Print all available tables."""
    uniqs = get_tables(**kwargs)
    _print_line(uniqs)


def schema(tab, index_only=False, **kwargs):
    """Print the schema for a given table"""
    if not isinstance(tab, Marble):
        table = Table.from_tag(tab, **kwargs)
    else:
        table = tab
    if kwargs.get('verbose'):
        _print_title("Columns of (IX=index, PT=partition) " + table._name)

    # decorate the fields with index and partition information
    cols = sorted([c.description() for c in table._columns.values()
                   if c.index_indicator or not index_only])
    _print_line(cols, width=100, alignments=[_ALG_LEFT] * len(cols))
    if kwargs.get('verbose'):
        _print_separator(100)


def partitions(table, **kwargs):
    """Print the partitions for a given table."""
    from hustle.core.settings import Settings
    settings = Settings(**kwargs)
    ddfs = settings["ddfs"]

    if isinstance(table, Marble):
        tablename = table._name
    else:
        tablename = table

    tags = ddfs.list(Table.base_tag(tablename) + ":")
    uniqs = set()
    for tag in tags:
        l = tag.find(':')
        r = tag.rfind(':')
        if r != l:
            uniqs.add(tag)
    _print_line(sorted(uniqs), width=132)


def _print_separator(width=80):
    print "- - " * (width / 4)


def _print_title(title, width=80):
    _print_separator(width)
    print title.rjust((width / 2) + len(title) / 2)
    _print_separator(width)


def _safe_str(s):
    try:
        return str(s)
    except UnicodeEncodeError:
        return unicode(s).encode("utf-8")


def _print_line(items, width=80, cols=3, alignments=None):
    i = 0
    line = ""
    if alignments is None:
        alignments = [_ALG_CENTER] * max(cols, len(items))
    for item, algn in zip(items, alignments):
        if type(item) in (int, long):
            item = "{:,}".format(item)
        else:
            item = _safe_str(item)

        if algn == _ALG_RIGHT:
            item = item.rjust(width / cols)
        elif algn == _ALG_LEFT:
            item = item.ljust(width / cols)
        else:
            item = item.center(width / cols)

        if i < cols:
            line = "%s %s" % (line, item) if line else item
            i += 1
        else:
            print line
            i, line = 1, item
    print line

def _get_blobs(table_or_expr, ddfs):
    # assume a table is being passed in
    table = table_or_expr
    where = None
    if hasattr(table_or_expr, 'table'):
        # nope, its a where clause (ie. and expr)
        table = table_or_expr.table
        where = table_or_expr

    if table._blobs:
        return table._blobs
    elif where and table._partition:
        # collect the blobs
        basetag = table.base_tag(table._name) + ':'
        tags = [tag[len(basetag):] for tag in ddfs.list(basetag)]
        seltags = [table.base_tag(table._name, part) for part in where.partition(tags)]
        rval = list()
        for tag in seltags:
            rval.extend(ddfs.blobs(tag))
        return rval
    else:
        tags = ddfs.list(table.base_tag(table._name))
        blobs = []
        for tag in tags:
            replicas = list(ddfs.blobs(tag))
            blobs.extend(replicas)
        return blobs



