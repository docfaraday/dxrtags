#!/usr/bin/python

from argparse import ArgumentParser
import string
from string import Template
import linecache

import os.path
import sys
import time

from dxr.config import Config
from dxr.utils import connect_db

# In brief, this script does the following:
#
# Searches for a dxr_config file; first it looks in the current working
# directory, and if not found, it changes to the parent directory and searches
# again. (Note: this is the same dxr_config file that dxrtags uses)
#
# Accepts a query including a query type (either files, refs, defs, or decls)
# and a token to perform the query on, and (optionally) the file name and line
# number where the token was found.
#
# Given this information, first we create temporary tables containing every
# function, macro, type, and variable that the token could be referring to.
# (this happens in find_matches_for_token_in). Eg. |foo| might be both a
# variable name and a function name.
#
# Then, we use these temporary tables to carry out the query type the user
# asked for:
#   refs -> Everything that references |foo|
#   defs -> The definition/s of |foo|
#   decls -> The declaration of |foo|
#   files -> Special case, just gets a list of matching files by name
#
# Lastly, all matches are output to dxr-ctags (file in working directory) in
# ctags format, allowing editors with ctags support to integrate.

def is_root(directory):
    return os.path.realpath(directory) == os.path.realpath(os.path.join(directory, '..'))

def at_root():
    return is_root(os.path.curdir)

def find_dxr_tree():
    # Hard-coded config file name; this is what dxrtags generates
    while not os.path.exists('dxr_config'):
        if at_root():
            print('Could not find dxr_config')
            return None
        os.chdir('..')

    # Ok, we have found a dxr_config file
    config = Config('dxr_config')

    if len(config.trees) is 1:
        return config.trees[0]

    # More than one tree in config file. Try to figure out which one corresponds
    # to the directory we're in.
    likely_treename = os.path.basename(os.path.abspath(os.path.curdir))

    # Ordinarily, this config file will only contain one tree, but if someone
    # hand-hacks theirs, forgive them
    for t in config.trees:
        if t.name == likely_treename:
            return t

    print('Found dxr_config, but could not determine our tree')
    return None

def clear_tags_file():
    tagfile_path = os.path.abspath('dxr-ctags')
    tagfile = open(tagfile_path, 'w')
    tagfile.write('')

# Takes query results, and writes them to a ctags format file.
# (This is the easiest way to get vim integration; we set up a bunch of bindings
# that will call this script with the necessary arguments, and then kick vim's
# ctags integration to pull in the results. A little weird, but it works.)
def query_and_write_tags_file(conn, query, token, sql_parameters = {}):
    should_explain = True
    start_time = None
    if should_explain:
        res = conn.execute("EXPLAIN QUERY PLAN " + query, sql_parameters)
        start_time = time.time() * 1000
        for row in res:
            print(row)

    res = conn.execute(query, sql_parameters)

    if start_time is not None:
        print((time.time() * 1000) - start_time)

    tagfile_path = os.path.abspath('dxr-ctags')
    tagfile = open(tagfile_path, 'a')
    for row in res:
        filename = row[0]
        line_number = row[1]
        column = row[2] # Not much we can do with this right now...
        qualname = row[3]
        # Would be very nice if dxr recorded line contents, this will be kinda
        # sad if line-numbers change, but GNU global does the same thing
        line = linecache.getline(filename, line_number).strip()
        tagfile.write("%s\t%s\t%d;\"\tqualname:<<<%s>>>\tline:%s \n" % (token, filename, line_number, qualname, line))

    tagfile.close()

def find_matches_for_token_in(conn,
                              table_to_search,
                              match_file_and_line_in,
                              token,
                              from_file,
                              from_line_start,
                              from_line_end):
    queries_to_union = []
    sql_parameters = {'token' : token}

    if from_file is None:
        query = """
            SELECT DISTINCT results.* FROM $table_to_search AS results 
            WHERE results.name ==:token
            """
        queries_to_union.append(Template(query).substitute(**locals()))
    else:
        query = """
            CREATE TEMPORARY TABLE IF NOT EXISTS matching_files AS
            SELECT files.id FROM files
            WHERE files.path LIKE :from_file
        """
        conn.execute(query, {'from_file' : '%' + from_file})

        # SQLite keeps picking bad query plans where matching_files isn't
        # the outer loop, even when it is a temp table of size 1.
        # So, we force the issue.
        query = """
            SELECT id FROM matching_files LIMIT 1
        """

        res = conn.execute(query)

        row = res.fetchone()

        if row is None:
            return None

        file_id = row[0]

        for table in match_file_and_line_in:
            table_with_file_and_line = table['table']
            join_key = table['join_key']
            query = """
                SELECT DISTINCT results.* FROM $table_to_search AS results
                INNER JOIN $table_with_file_and_line ON 
                    results.id == $table_with_file_and_line.$join_key
                WHERE $table_with_file_and_line.file_id  == $file_id
                    AND results.name=:token
            """

            if from_line_start is not None:
                if from_line_start == from_line_end:
                    sql_parameters.update({
                        'from_line' : from_line_start
                    })

                    query += """
                        AND $table_with_file_and_line.file_line == :from_line
                    """;

                else:
                    sql_parameters.update({
                        'from_line_start' : from_line_start,
                        'from_line_end' : from_line_end
                    })

                    query += """
                        AND $table_with_file_and_line.file_line BETWEEN :from_line_start AND :from_line_end 
                    """;

            queries_to_union.append(Template(query).substitute(**locals()))

    temp_table_name = 'matching_' + table_to_search + '_temp'

    final_query = 'CREATE TEMP TABLE ' + temp_table_name + ' AS ' + string.join(queries_to_union, ' UNION ') + ';'

    conn.execute(final_query, sql_parameters)
    res = conn.execute('SELECT * FROM ' + temp_table_name + ';');

    # Make it easy for caller to determine if the temporary table has anything
    # in it, so it can determine whether it needs to relax its search.
    if res.fetchone() is None:
        res = None
        conn.execute('DROP TABLE ' + temp_table_name)
        return None

    return temp_table_name

# Builds temporary tables holding matches for token. Easier to read than
# inner select, and more efficient since we need to reuse.
# Returns names of temporary tables
def find_matches_for_token(
        conn,
        token,
        from_file=None,
        from_line_start=None,
        from_line_end=None):

    # The SQL statements that find every matching variable, function, macro,
    # and type that the token might be referring to, declaring, or defining
    # (ie; "What exactly is this token?")

    # The file and line number of a token observed in a source file could be
    # recorded in many different tables, depending on how the token was
    # categorized
    matching_functions_table = find_matches_for_token_in(
            conn = conn,
            table_to_search = 'functions',
            match_file_and_line_in = [
                # Gratuitous join, but no big deal
                # Covers function definitions, and declarations for pure virtual
                # functions
                {'table' : 'functions',           'join_key' : 'id'}, 
                # Covers function references; this includes function calls, and
                # converting to function pointers.
                {'table' : 'function_refs',       'join_key' : 'refid'},
                # Covers function declarations, unless pure virtual
                {'table' : 'function_decldef',    'join_key' : 'defid'}
            ],
            token = token,
            from_file = from_file,
            from_line_start = from_line_start,
            from_line_end = from_line_end)


    matching_macros_table = find_matches_for_token_in(
            conn = conn,
            table_to_search = 'macros',
            match_file_and_line_in = [
                # Gratuitous join, but no big deal
                # Covers macro definitions
                {'table' : 'macros',           'join_key' : 'id'}, 
                # Covers macro references
                {'table' : 'macro_refs',       'join_key' : 'refid'},
            ],
            token = token,
            from_file = from_file,
            from_line_start = from_line_start,
            from_line_end = from_line_end)


    # BUG?: Stuff like "friend class Foo" is not recorded anywhere in dxr,
    # so contextual clues are worthless for them.
    matching_types_table = find_matches_for_token_in(
            conn = conn,
            table_to_search = 'types',
            match_file_and_line_in = [
                # Gratuitous join, but no big deal
                # Covers type definitions
                {'table' : 'types',           'join_key' : 'id'}, 
                # Covers type references
                {'table' : 'type_refs',       'join_key' : 'refid'},
            ],
            token = token,
            from_file = from_file,
            from_line_start = from_line_start,
            from_line_end = from_line_end)

    matching_typedefs_table = find_matches_for_token_in(
            conn = conn,
            table_to_search = 'typedefs',
            match_file_and_line_in = [
                # Gratuitous join, but no big deal
                # Covers type definitions
                {'table' : 'typedefs',          'join_key' : 'id'}, 
                # Covers type references
                {'table' : 'typedef_refs',       'join_key' : 'refid'},
            ],
            token = token,
            from_file = from_file,
            from_line_start = from_line_start,
            from_line_end = from_line_end)


    matching_variables_table = find_matches_for_token_in(
            conn = conn,
            table_to_search = 'variables',
            match_file_and_line_in = [
                # Gratuitous join, but no big deal
                # Covers variable definitions
                {'table' : 'variables',           'join_key' : 'id'}, 
                # Covers variable references
                {'table' : 'variable_refs',       'join_key' : 'refid'},
                # Covers variable declarations
                {'table' : 'variable_decldef',    'join_key' : 'defid'}
            ],
            token = token,
            from_file = from_file,
            from_line_start = from_line_start,
            from_line_end = from_line_end)

    if matching_functions_table is None and matching_macros_table is None and matching_types_table is None and matching_typedefs_table is None and matching_variables_table is None:
        if from_line_start is not None:
            print("Found no matches; try ignoring line number")
            return find_matches_for_token(conn, token, from_file)
        elif from_file is not None:
            print("Found no matches; try ignoring file name and line number")
            return find_matches_for_token(conn, token)

    return {
        'variables'    : matching_variables_table,
        'functions'    : matching_functions_table,
        'macros'       : matching_macros_table,
        'types'        : matching_types_table,
        'typedefs'     : matching_typedefs_table
    }

def query_for_refs(conn, token, from_file, from_line_start, from_line_end):
    matches = find_matches_for_token(conn, token, from_file, from_line_start, from_line_end)

    if matches['functions'] is not None:
        function_refs_query = Template("""
            SELECT files.path,
                   function_refs.file_line,
                   function_refs.file_col,
                   matching_functions.qualname
            FROM $matching_functions_table as matching_functions
            INNER JOIN function_refs ON function_refs.refid == matching_functions.id
            INNER JOIN files ON files.id == function_refs.file_id
            ORDER BY matching_functions.rowid;
        """)

        query_and_write_tags_file(conn, function_refs_query.substitute(matching_functions_table = matches['functions']), token)

    if matches['macros'] is not None:
        macro_refs_query = Template("""
            SELECT files.path,
                   macro_refs.file_line,
                   macro_refs.file_col,
                   matching_macros.name || matching_macros.args
            FROM $matching_macros_table AS matching_macros
            INNER JOIN macro_refs ON macro_refs.refid == matching_macros.id
            INNER JOIN files ON files.id == macro_refs.file_id
            ORDER BY matching_macros.rowid;
        """)

        query_and_write_tags_file(conn, macro_refs_query.substitute(matching_macros_table = matches['macros']), token)

    if matches['types'] is not None:
        type_refs_query = Template("""
            SELECT files.path,
                   type_refs.file_line,
                   type_refs.file_col,
                   matching_types.qualname
            FROM $matching_types_table AS matching_types
            INNER JOIN type_refs ON type_refs.refid == matching_types.id
            INNER JOIN files ON files.id == type_refs.file_id
            ORDER BY matching_types.rowid;
        """)

        query_and_write_tags_file(conn, type_refs_query.substitute(matching_types_table = matches['types']), token)

    if matches['typedefs'] is not None:
        typedef_refs_query = Template("""
            SELECT files.path,
                   typedef_refs.file_line,
                   typedef_refs.file_col,
                   matching_typedefs.qualname
            FROM $matching_typedefs_table AS matching_typedefs
            INNER JOIN typedef_refs ON typedef_refs.refid == matching_typedefs.id
            INNER JOIN files ON files.id == typedef_refs.file_id
            ORDER BY matching_typedefs.rowid;
        """)

        query_and_write_tags_file(conn, typedef_refs_query.substitute(matching_typedefs_table = matches['typedefs']), token)

    if matches['variables'] is not None:
        variable_refs_query = Template("""
            SELECT files.path,
                   variable_refs.file_line,
                   variable_refs.file_col,
                   matching_variables.qualname
            FROM $matching_variables_table as matching_variables
            INNER JOIN variable_refs ON variable_refs.refid == matching_variables.id
            INNER JOIN files ON files.id == variable_refs.file_id
            ORDER BY matching_variables.rowid;
        """)

        query_and_write_tags_file(conn, variable_refs_query.substitute(matching_variables_table = matches['variables']), token)


def query_for_defs(conn, token, from_file, from_line_start, from_line_end):
    matches = find_matches_for_token(conn, token, from_file, from_line_start, from_line_end)
    # First part gets the definition, second gets the definitions of all
    # overrides, third picks up inline functions (these are not recorded in
    # function_decldef)
    if matches['functions'] is not None:
        function_defs_query = Template("""
            SELECT files.path,
                   function_decldef.definition_file_line,
                   function_decldef.definition_file_col,
                   matching_functions.qualname,
                   matching_functions.rowid
            FROM $matching_functions_table AS matching_functions
            INNER JOIN function_decldef ON function_decldef.defid == matching_functions.id
            INNER JOIN files ON files.id == function_decldef.definition_file_id
            UNION
            SELECT files.path,
                   function_decldef.definition_file_line,
                   function_decldef.definition_file_col,
                   functions.qualname,
                   matching_functions.rowid
            FROM $matching_functions_table AS matching_functions
            INNER JOIN targets ON targets.targetid == -matching_functions.id AND targets.targetid != -targets.funcid
            INNER JOIN functions ON functions.id == targets.funcid
            INNER JOIN function_decldef ON function_decldef.defid == functions.id
            INNER JOIN files ON files.id == function_decldef.definition_file_id
            UNION
            SELECT files.path,
                   matching_functions.file_line,
                   matching_functions.file_col,
                   matching_functions.qualname,
                   matching_functions.rowid
            FROM $matching_functions_table AS matching_functions
            LEFT JOIN function_decldef ON function_decldef.defid == matching_functions.id
            INNER JOIN files ON files.id == matching_functions.file_id
                WHERE function_decldef.defid IS NULL
            ORDER BY matching_functions.rowid;
        """)

        query_and_write_tags_file(conn, function_defs_query.substitute(matching_functions_table = matches['functions']), token)

    if matches['macros'] is not None:
        macro_defs_query = Template("""
            SELECT files.path,
                   matching_macros.file_line,
                   matching_macros.file_col,
                   matching_macros.name || matching_macros.args
            FROM $matching_macros_table AS matching_macros
            INNER JOIN files ON files.id == matching_macros.file_id
            ORDER BY matching_macros.rowid;
        """)

        query_and_write_tags_file(conn, macro_defs_query.substitute(matching_macros_table = matches['macros']), token)

    if matches['types'] is not None:
        type_defs_query = Template("""
            SELECT files.path,
                   matching_types.file_line,
                   matching_types.file_col,
                   matching_types.qualname
            FROM $matching_types_table AS matching_types
            INNER JOIN files ON files.id == matching_types.file_id
            ORDER BY matching_types.rowid;
        """)

        query_and_write_tags_file(conn, type_defs_query.substitute(matching_types_table = matches['types']), token)

    if matches['typedefs'] is not None:
        typedef_defs_query = Template("""
            SELECT files.path,
                   matching_typedefs.file_line,
                   matching_typedefs.file_col,
                   matching_typedefs.qualname
            FROM $matching_typedefs_table AS matching_typedefs
            INNER JOIN files ON files.id == matching_typedefs.file_id
            ORDER BY matching_typedefs.rowid;
        """)

        query_and_write_tags_file(conn, typedef_defs_query.substitute(matching_typedefs_table = matches['typedefs']), token)

    if matches['variables'] is not None:
        variable_defs_query = Template("""
            SELECT files.path,
                   variable_decldef.definition_file_line,
                   variable_decldef.definition_file_col,
                   matching_variables.qualname,
                   matching_variables.rowid
            FROM $matching_variables_table AS matching_variables
            INNER JOIN variable_decldef ON variable_decldef.defid == matching_variables.id
            INNER JOIN files ON files.id == variable_decldef.definition_file_id
            UNION
            SELECT files.path,
                   matching_variables.file_line,
                   matching_variables.file_col,
                   matching_variables.qualname,
                   matching_variables.rowid
            FROM $matching_variables_table AS matching_variables
            INNER JOIN files ON files.id == matching_variables.file_id
            ORDER BY matching_variables.rowid;
        """)

        query_and_write_tags_file(conn, variable_defs_query.substitute(matching_variables_table = matches['variables']), token)


def query_for_decls(conn, token, from_file, from_line_start, from_line_end):
    matches = find_matches_for_token(conn, token, from_file, from_line_start, from_line_end)


# |function_decldef| tells us about declarations unless the declaration is pure
# virtual, in which case |functions| points at the declaration (|functions|
# normally points at the definition). A little weird, and possibly not
# intentional. This might need to change.
    if matches['functions'] is not None:
        function_decls_query = Template("""
            SELECT files.path,
                   function_decldef.file_line,
                   function_decldef.file_col,
                   matching_functions.qualname,
                   matching_functions.rowid
            FROM $matching_functions_table AS matching_functions
            INNER JOIN function_decldef ON function_decldef.defid == matching_functions.id
            INNER JOIN files ON files.id == function_decldef.file_id
            UNION
            SELECT files.path,
                   matching_functions.file_line,
                   matching_functions.file_col,
                   matching_functions.qualname,
                   matching_functions.rowid
            FROM $matching_functions_table AS matching_functions
            LEFT JOIN function_decldef ON function_decldef.defid == matching_functions.id
            INNER JOIN files ON files.id == matching_functions.file_id
            WHERE function_decldef.defid IS NULL
            ORDER BY matching_functions.rowid;
        """)

        query_and_write_tags_file(conn, function_decls_query.substitute(matching_functions_table = matches['functions']), token)

    if matches['macros'] is not None:
        macro_decls_query = Template("""
            SELECT files.path,
                   matching_macros.file_line,
                   matching_macros.file_col,
                   matching_macros.name || matching_macros.args
            FROM $matching_macros_table AS matching_macros
            INNER JOIN files ON files.id == matching_macros.file_id
            ORDER BY matching_macros.rowid;
        """)

        query_and_write_tags_file(conn, macro_decls_query.substitute(matching_macros_table = matches['macros']), token)

    if matches['types'] is not None:
        type_decls_query = Template("""
            SELECT files.path,
                   matching_types.file_line,
                   matching_types.file_col,
                   matching_types.qualname
            FROM $matching_types_table AS matching_types
            INNER JOIN files ON files.id == matching_types.file_id
            ORDER BY matching_types.rowid;
        """)

        query_and_write_tags_file(conn, type_decls_query.substitute(matching_types_table = matches['types']), token)

    if matches['typedefs'] is not None:
        typedef_decls_query = Template("""
            SELECT files.path,
                   matching_typedefs.file_line,
                   matching_typedefs.file_col,
                   matching_typedefs.qualname
            FROM $matching_typedefs_table AS matching_typedefs
            INNER JOIN files ON files.id == matching_typedefs.file_id
            ORDER BY matching_typedefs.rowid;
        """)

        query_and_write_tags_file(conn, typedef_decls_query.substitute(matching_typedefs_table = matches['typedefs']), token)

# BUG: member variables are never put into variable_decldef, but only in variables.
# There might be some way to build a query that only picks up member variables,
# but I doubt there is a way to make it distinguish static class scope variables.
# BUG: When a variable comes in as a parameter to a function, it is not recorded in
# variable_decldef. For all intents and purposes, this should be treated as a declaration
# (the user asks, "Where is some_param declared?")
    if matches['variables'] is not None:
        variable_decls_query = Template("""
            SELECT files.path,
                   variable_decldef.file_line,
                   variable_decldef.file_col,
                   matching_variables.qualname
            FROM $matching_variables_table AS matching_variables
            INNER JOIN variable_decldef ON variable_decldef.defid == matching_variables.id
            INNER JOIN files ON files.id == variable_decldef.file_id
            ORDER BY matching_variables.rowid;
        """)

        query_and_write_tags_file(conn, variable_decls_query.substitute(matching_variables_table = matches['variables']), token)

def query_for_files(conn, token, from_file, from_line_start, from_line_end):
    query = """
        SELECT files.path,
               0,
               0,
               files.path
        FROM files
        WHERE files.path LIKE :token;
    """

    query_and_write_tags_file(conn, query, token, {'token' : '%' + token})

def main():
    debugfile_path = os.path.abspath('/tmp/dxr-ctags.out')
    debugfile = open(debugfile_path, 'w')
    debugfile.write(string.join(sys.argv) + "\n")
    debugfile.write(os.path.abspath(os.path.curdir))

    dxr_tree = find_dxr_tree()
    if dxr_tree is None:
        return 1

    conn = connect_db(dxr_tree.target_folder)

    clear_tags_file()

    query_functions = {
        'defs'  : query_for_defs,
        'decls' : query_for_decls,
        'refs'  : query_for_refs,
        'files'  : query_for_files
    }

    parser = ArgumentParser(description='Parse command-line arguments for dxrtags')
    parser.add_argument('-t', '--token', help='The token to search for', required=True)
    parser.add_argument('-q', '--query_type', choices=query_functions.keys(), help='The type of query to perform', required=True)
    parser.add_argument('-f', '--from_file', help='The file the token was discovered in')
    parser.add_argument('-l', '--from_line', type=int, help='The line the token was discovered on')
    parser.add_argument('-w', '--wiggle_room', type=int, default=0, help='Wiggle room for line number')
    args = parser.parse_args()

    from_line_start = args.from_line;
    from_line_end = args.from_line;
    if args.wiggle_room is not None and args.from_line is not None:
        from_line_start -= args.wiggle_room
        from_line_end += args.wiggle_room

    # Trim off leading path
    file_from_here = None
    leading_path = args.from_file
    trailing_path = None

    # Example:
    # leading_path = /tmp/snapshot.9p-8uq348ihj9d289/directory-in-source-tree/file_we_are_interested_in.c
    while leading_path is not None and not is_root(leading_path) and leading_path is not "":
        print(leading_path)
        next_trailing_path = None
        (leading_path, next_trailing_path) = os.path.split(leading_path)
        print(leading_path)
        print(next_trailing_path)
        # iter 0 (/tmp/snapshot.9p-8uq348ihj9d289/directory-in-source-tree, file_we_are_interested_in.c)
        # iter 1 (/tmp/snapshot.9p-8uq348ihj9d289, directory-in-source-tree)
        if trailing_path is None:
            trailing_path = next_trailing_path
        else:
            trailing_path = os.path.join(next_trailing_path, trailing_path)

        print("Does " + trailing_path + " exist?")
        # iter 0 file_we_are_interested_in.c
        # iter 1 directory-in-source-tree/file_we_are_interested_in.c
        if os.path.exists(trailing_path):
            # iter 0 False
            # iter 1 True
            file_from_here = trailing_path
            # Keep iterating, since we might have the same filename in multiple
            # places, so we want to keep an eye out for better matches (ie;
            # has a longer matching path prefix).

    if file_from_here is not None:
        print("Using " + file_from_here)

    query_functions[args.query_type](conn, args.token, file_from_here, from_line_start, from_line_end);
    return 0

if __name__ == '__main__':
    main()

# vim: softtabstop=4:shiftwidth=4:expandtab
