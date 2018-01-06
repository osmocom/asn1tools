import sys
import unittest
import importlib

import asn1tools

sys.path.append('tests/files')
sys.path.append('tests/files/ietf')
sys.path.append('tests/files/3gpp')


class Asn1ToolsParseTest(unittest.TestCase):

    maxDiff = None

    def parse_and_verify(self, module, path='.'):
        asn_path = 'tests/files/' + path + '/' + module + '.asn'
        actual = asn1tools.parse_files(asn_path)

        # from pprint import pformat
        #
        # py_path = 'tests/files/' + path + '/' + module + '.py'
        #
        # with open(py_path, 'w') as fout:
        #     fout.write('EXPECTED = ' + pformat(actual))

        module = importlib.import_module(module)
        self.assertEqual(actual, module.EXPECTED)

    def test_parse_foo(self):
        self.parse_and_verify('foo')

    def test_parse_bar(self):
        self.parse_and_verify('bar')

    def test_parse_all_types(self):
        self.parse_and_verify('all_types')

    def test_parse_information_object(self):
        self.parse_and_verify('information_object')

    def test_parse_x680(self):
        self.parse_and_verify('x680')

    def test_parse_x691_a1(self):
        self.parse_and_verify('x691_a1')

    def test_parse_x691_a2(self):
        with self.assertRaises(AssertionError):
            self.parse_and_verify('x691_a2')

    def test_parse_x691_a3(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            self.parse_and_verify('x691_a3')

        self.assertEqual(
            str(cm.exception),
            "Invalid ASN.1 syntax at line 10, column 22: 'SEQUENCE >!<"
            "(SIZE(2, ...)) OF ChildInformation OPTIONAL,': Expected \"{\".")

    def test_parse_x691_a4(self):
        self.parse_and_verify('x691_a4')

    def test_parse_zforce(self):
        self.parse_and_verify('zforce')

    def test_parse_rrc_8_6_0(self):
        self.parse_and_verify('rrc_8_6_0', '3gpp')

    def test_parse_rrc_14_4_0(self):
        self.parse_and_verify('rrc_14_4_0', '3gpp')

    def test_parse_s1ap_14_4_0(self):
        self.parse_and_verify('s1ap_14_4_0', '3gpp')

    def test_parse_lpp_14_3_0(self):
        self.parse_and_verify('lpp_14_3_0', '3gpp')

    def test_parse_rfc1155(self):
        self.parse_and_verify('rfc1155', 'ietf')

    def test_parse_rfc1157(self):
        self.parse_and_verify('rfc1157', 'ietf')

    def test_parse_rfc2986(self):
        self.parse_and_verify('rfc2986', 'ietf')

    def test_parse_rfc3161(self):
        self.parse_and_verify('rfc3161', 'ietf')

    def test_parse_rfc3279(self):
        self.parse_and_verify('rfc3279', 'ietf')

    def test_parse_rfc3281(self):
        self.parse_and_verify('rfc3281', 'ietf')

    def test_parse_rfc3447(self):
        self.parse_and_verify('rfc3447', 'ietf')

    def test_parse_rfc3852(self):
        self.parse_and_verify('rfc3852', 'ietf')

    def test_parse_rfc4210(self):
        self.parse_and_verify('rfc4210', 'ietf')

    def test_parse_rfc4211(self):
        self.parse_and_verify('rfc4211', 'ietf')

    def test_parse_rfc4511(self):
        self.parse_and_verify('rfc4511', 'ietf')

    def test_parse_rfc5280(self):
        self.parse_and_verify('rfc5280', 'ietf')

    def test_parse_imports_global_module_reference(self):
        actual = asn1tools.parse_string('A DEFINITIONS ::= BEGIN '
                                        'IMPORTS '
                                        'a FROM B '
                                        'c, d FROM E global-module-reference '
                                        'f, g FROM H {iso(1)}; '
                                        'END')

        expected = {
            'A': {
                'extensibility-implied': False,
                'imports': {
                    'B': ['a'],
                    'E': ['c', 'd'],
                    'H': ['f', 'g']
                },
                'object-classes': {},
                'object-sets': {},
                'types': {},
                'values': {}
            }
        }

        self.assertEqual(actual, expected)

    def test_parse_error_empty_string(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('')

        self.assertEqual(str(cm.exception),
                         "Invalid ASN.1 syntax at line 1, column 1: '>!<': "
                         "Expected modulereference.")

    def test_parse_error_begin_missing(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= END')

        self.assertEqual(str(cm.exception),
                         "Invalid ASN.1 syntax at line 1, column 19: "
                         "'A DEFINITIONS ::= >!<END': Expected BEGIN.")

    def test_parse_error_end_missing(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN')

        self.assertEqual(str(cm.exception),
                         "Invalid ASN.1 syntax at line 1, column 24: "
                         "'A DEFINITIONS ::= BEGIN>!<': Expected END.")

    def test_parse_error_type_assignment_missing_assignment(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN A END')

        self.assertEqual(str(cm.exception),
                         "Invalid ASN.1 syntax at line 1, column 27: "
                         "'A DEFINITIONS ::= BEGIN A >!<END': "
                         "Expected ::=.")

    def test_parse_error_value_assignment_missing_assignment(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN a INTEGER END')

        self.assertEqual(str(cm.exception),
                         "Invalid ASN.1 syntax at line 1, column 35: "
                         "'A DEFINITIONS ::= BEGIN a INTEGER >!<END': "
                         "Expected ::=.")

    def test_parse_error_sequence_missing_type(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN'
                                   '  A ::= SEQUENCE { a } '
                                   'END')

        self.assertEqual(
            str(cm.exception),
            "Invalid ASN.1 syntax at line 1, column 45: 'A DEFINITIONS ::= BEGIN "
            " A ::= SEQUENCE { a >!<} END': Expected Type.")

    def test_parse_error_sequence_missing_member_name(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN'
                                   '  A ::= SEQUENCE { A } '
                                   'END')

        self.assertEqual(
            str(cm.exception),
            "Invalid ASN.1 syntax at line 1, column 43: 'A DEFINITIONS ::= "
            "BEGIN  A ::= SEQUENCE { >!<A } END': Expected \"}\".")

    def test_parse_error_definitive_identifier(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A {} DEFINITIONS ::= BEGIN '
                                   'END')

        self.assertEqual(
            str(cm.exception),
            "Invalid ASN.1 syntax at line 1, column 4: 'A {>!<} DEFINITIONS "
            "::= BEGIN END': Expected {{identifier Suppress:(\"(\") - "
            "definitiveNumberForm - Suppress:(\")\")} | identifier | "
            "definitiveNumberForm}.")

    def test_parse_error_missing_union_member_beginning(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN '
                                   'B ::= INTEGER (| SIZE (1))'
                                   'END')

        self.assertEqual(
            str(cm.exception),
            "Invalid ASN.1 syntax at line 1, column 40: 'A DEFINITIONS ::= BEGIN "
            "B ::= INTEGER (>!<| SIZE (1))END': Expected one or more constraints.")

    def test_parse_error_missing_union_member_middle(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN '
                                   'B ::= INTEGER (SIZE (1) | | (0))'
                                   'END')

        self.assertEqual(
            str(cm.exception),
            "Invalid ASN.1 syntax at line 1, column 49: \'A DEFINITIONS "
            "::= BEGIN B ::= INTEGER (SIZE (1) >!<| | (0))END\': Expected \")\".")

    def test_parse_error_missing_union_member_end(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN '
                                   'B ::= INTEGER (SIZE (1) |)'
                                   'END')

        self.assertEqual(
            str(cm.exception),
            "Invalid ASN.1 syntax at line 1, column 49: \'A DEFINITIONS "
            "::= BEGIN B ::= INTEGER (SIZE (1) >!<|)END\': Expected \")\".")

    def test_parse_error_size_constraint_missing_parentheses(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN '
                                   'B ::= INTEGER (SIZE 1)'
                                   'END')

        self.assertEqual(
            str(cm.exception),
            "Invalid ASN.1 syntax at line 1, column 45: \'A DEFINITIONS ::= "
            "BEGIN B ::= INTEGER (SIZE >!<1)END\': Expected \"(\".")

    def test_parse_error_size_constraint_missing_size(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN '
                                   'B ::= INTEGER (SIZE ())'
                                   'END')

        self.assertEqual(
            str(cm.exception),
            "Invalid ASN.1 syntax at line 1, column 46: 'A DEFINITIONS ::= "
            "BEGIN B ::= INTEGER (SIZE (>!<))END': Expected one or more "
            "constraints.")

    def test_parse_error_tag_class_number_missing(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN '
                                   'B ::= [] INTEGER '
                                   'END')

        self.assertEqual(
            str(cm.exception),
            "Invalid ASN.1 syntax at line 1, column 32: 'A DEFINITIONS "
            "::= BEGIN B ::= [>!<] INTEGER END': Expected ClassNumber.")

    def test_parse_error_missing_type(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS ::= BEGIN '
                                   'B ::= '
                                   'END')

        self.assertEqual(
            str(cm.exception),
            "Invalid ASN.1 syntax at line 1, column 31: 'A DEFINITIONS ::= BEGIN "
            "B ::= >!<END': Expected Type.")

    def test_parse_error_end_missing_with_comments(self):
        with self.assertRaises(asn1tools.ParseError) as cm:
            asn1tools.parse_string('A DEFINITIONS -- g -- \n'
                                   '-- hhhh\n'
                                   '::= BEGIN ')

        self.assertEqual(str(cm.exception),
                         "Invalid ASN.1 syntax at line 3, column 11: "
                         "'::= BEGIN >!<': Expected END.")


if __name__ == '__main__':
    unittest.main()
