"""Basic Encoding Rules (BER) codec.

"""

import time
import math
import binascii
from copy import copy
import datetime
from collections import OrderedDict

from ..parser import EXTENSION_MARKER
from . import BaseType, format_bytes, DecodeError, ErrorWithLocation
from . import EncodeError
from . import DecodeError
from . import format_or
from . import compiler
from . import utc_time_to_datetime
from . import utc_time_from_datetime
from . import generalized_time_to_datetime
from . import generalized_time_from_datetime
from .compiler import enum_values_as_dict
from .compiler import clean_bit_string_value


class Class(object):
    UNIVERSAL        = 0x00
    APPLICATION      = 0x40
    CONTEXT_SPECIFIC = 0x80
    PRIVATE          = 0xc0


class Encoding(object):
    PRIMITIVE   = 0x00
    CONSTRUCTED = 0x20


class Tag(object):
    END_OF_CONTENTS   = 0x00
    BOOLEAN           = 0x01
    INTEGER           = 0x02
    BIT_STRING        = 0x03
    OCTET_STRING      = 0x04
    NULL              = 0x05
    OBJECT_IDENTIFIER = 0x06
    OBJECT_DESCRIPTOR = 0x07
    EXTERNAL          = 0x08
    REAL              = 0x09
    ENUMERATED        = 0x0a
    EMBEDDED_PDV      = 0x0b
    UTF8_STRING       = 0x0c
    RELATIVE_OID      = 0x0d
    SEQUENCE          = 0x10
    SET               = 0x11
    NUMERIC_STRING    = 0x12
    PRINTABLE_STRING  = 0x13
    T61_STRING        = 0x14
    VIDEOTEX_STRING   = 0x15
    IA5_STRING        = 0x16
    UTC_TIME          = 0x17
    GENERALIZED_TIME  = 0x18
    GRAPHIC_STRING    = 0x19
    VISIBLE_STRING    = 0x1a
    GENERAL_STRING    = 0x1b
    UNIVERSAL_STRING  = 0x1c
    CHARACTER_STRING  = 0x1d
    BMP_STRING        = 0x1e
    DATE              = 0x1f
    TIME_OF_DAY       = 0x20
    DATE_TIME         = 0x21


END_OF_CONTENTS_OCTETS = b'\x00\x00'
TAG_MISMATCH = object()


def flatten(l):
    """
    Flatten irregular nested list
    :param l:
    :return:
    """
    if isinstance(l, (list, tuple)):
        return [a for i in l for a in flatten(i)]
    else:
        return [l]


def is_end_of_data(data, offset, end_offset):
    # Detect end of data
    if end_offset is not None:
        if offset >= end_offset:
            return True, offset

    elif detect_end_of_contents_tag(data, offset):
        return True, offset + 2

    return False, offset


def detect_end_of_contents_tag(data, offset):
    """
    Determine whether end of contents tag is present at offset in data
    :param bytes data:
    :param int offset:
    :return:
    """
    two_bytes = data[offset:offset + 2]
    if two_bytes == END_OF_CONTENTS_OCTETS:
        return True
    # Detect missing data
    elif len(two_bytes) != 2:
        raise OutOfByteDataError(
            'Ran out of data when trying to find End of Contents tag for indefinite length field',
            offset=offset)
    else:
        return False


def check_decode_error(asn_type, decoded_value, data, offset):
    """
    Checks if decode result corresponds to TAG_MISMATCH, if so, raise DecodeTagError
    :return:
    """
    if decoded_value == TAG_MISMATCH:
        raise DecodeTagError(asn_type, data, offset, location=asn_type)


class MissingMandatoryFieldError(DecodeError):
    """
    Error for when there is no data for a mandatory field member
    """
    def __init__(self, member, offset):
        super().__init__('{} is missing and has no default value'.format(member),
                         offset=offset,
                         location=member)


class OutOfByteDataError(DecodeError):
    """
    Error for when running out of / missing data missing when attempting to decode
    """
    pass


class MissingDataError(OutOfByteDataError):
    """
    Special variant of OutOfByteDataError for when remaining data length is less than decoded element length
    """
    def __init__(self, message, offset, expected_length, location=None):
        super().__init__(message, offset, location=location)
        self.expected_length = expected_length


class DecodeTagError(DecodeError):
    """
    ASN.1 tag decode error for BER and DER codecs
    """

    def __init__(self, asn_type, data, offset, location=None):
        """

        :param StandardDecodeMixin, Type asn_type: ASN type instance error occurred for
        :param bytes data: ASN data
        :param int offset:
        :param str location: Name of ASN1 element error occurred in
        """
        self.actual_tag = format_bytes(read_tag(data, offset)
                                       if asn_type.tag_len is None
                                       else data[offset:offset + asn_type.tag_len])
        self.asn_type = asn_type
        tag = asn_type.format_tag()
        message = "Expected {} with {}, but got '{}'.".format(
            asn_type.type_label(),
            'tags {}'.format(tag) if isinstance(tag, list) else "tag '{}'".format(tag),
            self.actual_tag)
        super(DecodeTagError, self).__init__(message, offset=offset, location=location)


class NoEndOfContentsTagError(DecodeError):
    """
    Exception for when end-of-contents tag (00) could not be found for indefinite-length field
    """
    pass


def encode_length_definite(length):
    if length <= 127:
        encoded = bytearray([length])
    else:
        encoded = bytearray()

        while length > 0:
            encoded.append(length & 0xff)
            length >>= 8

        encoded.append(0x80 | len(encoded))
        encoded.reverse()

    return encoded


def decode_length(encoded, offset, enforce_definite=True):
    """
    Decode definite or indefinite length of an ASN.1 node
    :param bytes encoded:
    :param int offset:
    :param bool enforce_definite: Whether to raise error if length is indefinite
    :return:
    """
    try:
        length = encoded[offset]
    except IndexError:
        raise OutOfByteDataError('Ran out of data when trying to read length',
                                 offset=offset)

    offset += 1

    if length & 0x80:   # Faster than > 127
        # Handle indefinite length
        if length == 128:
            if enforce_definite:
                raise DecodeError('Expected definite length, but got indefinite.',
                                  offset=offset - 1)

            return None, offset

        else:
            # Handle long length
            number_of_bytes = (length & 0x7f)
            encoded_length = encoded[offset:number_of_bytes + offset]

            # Verify all the length bytes exist
            if len(encoded_length) != number_of_bytes:
                raise OutOfByteDataError('Expected {} length byte(s) at offset {}, but got {}.'.format(
                        number_of_bytes, offset, len(encoded_length)),
                    offset=offset)

            length = int(binascii.hexlify(encoded_length), 16)
            offset += number_of_bytes

    # Detect missing data
    data_length = len(encoded)
    if offset + length > data_length:
        raise MissingDataError(
            'Expected at least {} contents byte(s), but got {}.'.format(length, data_length - offset),
            offset,
            length
        )

    return length, offset


def encode_signed_integer(number):
    byte_length = (8 + (number + (number < 0)).bit_length()) // 8
    return number.to_bytes(length=byte_length, byteorder='big', signed=True)


def encode_tag(number, flags):
    if number < 31:
        tag = bytearray([flags | number])
    else:
        tag = bytearray([flags | 0x1f])
        encoded = bytearray()

        while number > 0:
            encoded.append(0x80 | (number & 0x7f))
            number >>= 7

        encoded[0] &= 0x7f
        encoded.reverse()
        tag.extend(encoded)

    return tag


def skip_tag(data, offset):
    try:
        byte = data[offset]
        offset += 1

        if byte & 0x1f == 0x1f:
            while data[offset] & 0x80:
                offset += 1

            offset += 1
    except IndexError:
        raise OutOfByteDataError('Ran out of data when reading tag',
                                 offset=offset)

    if offset >= len(data):
        raise OutOfByteDataError('Ran out of data when reading tag',
                                 offset=offset)

    return offset


def read_tag(data, offset):
    return data[offset:skip_tag(data, offset)]


def skip_tag_length_contents(data, offset):
    """
    Get offset position at end of node (skip tag, length and contents)
    :param data:
    :param offset:
    :return:
    """
    offset = skip_tag(data, offset)

    return sum(decode_length(data, offset))


def encode_real(data):
    if data == float('inf'):
        data = b'\x40'
    elif data == float('-inf'):
        data = b'\x41'
    elif math.isnan(data):
        data = b'\x42'
    elif data == 0.0:
        data = b''
    else:
        if data >= 0:
            negative_bit = 0
        else:
            negative_bit = 0x40
            data *= -1

        mantissa, exponent = math.frexp(abs(data))
        mantissa = int(mantissa * 2 ** 53)
        lowest_set_bit = compiler.lowest_set_bit(mantissa)
        mantissa >>= lowest_set_bit
        mantissa |= (0x80 << (8 * ((mantissa.bit_length() // 8) + 1)))
        mantissa = binascii.unhexlify(hex(mantissa)[4:].rstrip('L'))
        exponent = (52 - lowest_set_bit - exponent)

        if -129 < exponent < 128:
            exponent = [0x80 | negative_bit, ((0xff - exponent) & 0xff)]
        elif -32769 < exponent < 32768:
            exponent = ((0xffff - exponent) & 0xffff)
            exponent = [0x81 | negative_bit, (exponent >> 8), exponent & 0xff]
        else:
            raise NotImplementedError(
                'REAL exponent {} out of range.'.format(exponent))

        data = bytearray(exponent) + mantissa

    return data


def decode_real_binary(control, data):
    if control in [0x80, 0xc0]:
        exponent = data[1]

        if exponent & 0x80:
            exponent -= 0x100

        offset = 2
    elif control in [0x81, 0xc1]:
        exponent = ((data[1] << 8) | data[2])

        if exponent & 0x8000:
            exponent -= 0x10000

        offset = 3
    else:
        raise DecodeError(
            'Unsupported binary REAL control word 0x{:02x}.'.format(control))

    mantissa = int(binascii.hexlify(data[offset:]), 16)
    decoded = float(mantissa * 2 ** exponent)

    if control & 0x40:
        decoded *= -1

    return decoded


def decode_real_special(control):
    try:
        return {
            0x40: float('inf'),
            0x41: float('-inf'),
            0x42: float('nan'),
            0x43: -0.0
        }[control]
    except KeyError:
        raise DecodeError(
            'Unsupported special REAL control word 0x{:02x}.'.format(control))


def decode_real_decimal(data):
    return float(data[1:].replace(b',', b'.'))


def decode_real(data):
    if len(data) == 0:
        decoded = 0.0
    else:
        control = data[0]

        if control & 0x80:
            decoded = decode_real_binary(control, data)
        elif control & 0x40:
            decoded = decode_real_special(control)
        else:
            decoded = decode_real_decimal(data)

    return decoded


def encode_object_identifier(data):
    identifiers = [int(identifier) for identifier in data.split('.')]

    first_subidentifier = (40 * identifiers[0] + identifiers[1])
    encoded_subidentifiers = encode_object_identifier_subidentifier(
        first_subidentifier)

    for identifier in identifiers[2:]:
        encoded_subidentifiers += encode_object_identifier_subidentifier(
            identifier)

    return encoded_subidentifiers


def encode_object_identifier_subidentifier(subidentifier):
    encoded = [subidentifier & 0x7f]
    subidentifier >>= 7

    while subidentifier > 0:
        encoded.append(0x80 | (subidentifier & 0x7f))
        subidentifier >>= 7

    return encoded[::-1]


def decode_object_identifier(data, offset, end_offset):
    subidentifier, offset = decode_object_identifier_subidentifier(data,
                                                                   offset)
    decoded = [subidentifier // 40, subidentifier % 40]

    while offset < end_offset:
        subidentifier, offset = decode_object_identifier_subidentifier(data,
                                                                       offset)
        decoded.append(subidentifier)

    return '.'.join([str(v) for v in decoded])


def decode_object_identifier_subidentifier(data, offset):
    decoded = 0

    while data[offset] & 0x80:
        decoded += (data[offset] & 0x7f)
        decoded <<= 7
        offset += 1

    decoded += data[offset]

    return decoded, offset + 1


class Type(BaseType):
    """
    Base type class for BER types
    """
    def __init__(self, name, type_name, number, flags=0):
        """

        :param str name: Name of type instance
        :param str type_name: ASN1 Type name
        :param int number: Tag number
        :param flags:
        """
        super().__init__(name, type_name)
        if number is None:
            self.tag = None
            self.tag_len = None
        else:
            self.tag = encode_tag(number, flags)
            self.tag_len = len(self.tag)

    def decode(self, data, offset, values=None):
        """
        Decode type value from byte data
        :param bytearray data: Binary ASN1 data to decode
        :param int offset: Current byte offset
        :param dict values:
        :return: Tuple of (decoded_value, end_offset)
        """
        raise NotImplementedError()

    def encode(self, data, encoded, values=None):
        """
        Encode value into byte data
        :param data: Value to be encoded
        :param bytearray encoded: Existing byte data to add encoded data to
        :param values:
        :return: None (extend 'encoded' bytearray)
        """
        raise NotImplementedError()

    def set_tag(self, number, flags):
        self.tag = encode_tag(number, flags)
        self.tag_len = len(self.tag)

    def format_tag(self):
        """
        Get formatted hex string representation of this type's tag
        :return:
        """
        return format_bytes(self.tag) if self.tag is not None else '(Unknown)'

    def set_size_range(self, minimum, maximum, has_extension_marker):
        pass


class StandardDecodeMixin(object):
    """
    Type class mixin for standard decoding logic
    (single predefined tag to be matched against, then decode length and contents)
    """
    indefinite_allowed = False  # Whether indefinite length encoding is allowed for this type

    def decode(self, data, offset, values=None):
        """
        Implements standard decode logic (single predefined tag to be matched against, then decode length and contents)
        :param bytearray data: Binary ASN1 data to decode
        :param int offset: Current byte offset
        :param dict values:
        :return: Tuple of (decoded_value, end_offset)
        """
        start_offset = offset
        offset += self.tag_len

        # Validate tag
        tag_data = data[start_offset:offset]
        if tag_data != self.tag:
            # Check for missing data
            if len(tag_data) != self.tag_len:
                raise OutOfByteDataError('Ran out of data when reading tag',
                                         offset=start_offset)
            # return TAG_MISMATCH Instead of raising DecodeTagError for better performance so that MembersType does
            # not have to catch exception for every missing optional type
            # CompiledType.decode_with_length() will detect TAG_MISMATCH returned value and raise appropriate exception
            return TAG_MISMATCH, start_offset

        # Decode length
        length, offset = decode_length(data, offset, enforce_definite=not self.indefinite_allowed)

        return self.decode_content(data, offset, length)

    def decode_content(self, data, offset, length):
        """
        Type-specific logic to decode content
        :param bytearray data: Binary data to decode
        :param int offset: Offset for start of content bytes
        :param int length: Length of content bytes (None if indefinite)
        :return: Tuple of (decoded_value, end_offset)
        """
        raise NotImplementedError('Type {} does not implement decode_content() method'.format(type(self).__name__))


class StandardEncodeMixin(object):
    """
    Type class mixin for standard encoding logic (append tag + length(content) + content)
    """
    def encode(self, data, encoded, values=None):
        """
        Encode value into byte data
        :param data: Value to be encoded
        :param bytearray encoded: Existing byte data to add encoded data to
        :param values:
        :return:
        """
        encoded_data = bytearray(self.encode_content(data, values=values))
        encoded.extend(self.tag + encode_length_definite(len(encoded_data)) + encoded_data)

    def encode_content(self, data, values=None):
        """
        Encode data value into bytearray
        :param data:
        :param values:
        :return:
        """
        raise NotImplementedError()


class PrimitiveOrConstructedType(Type):
    """
    Base type class for types which can be either primitive or constructed (BitString, OctetString, String)
    """

    def __init__(self, name, type_name, number, segment, flags=0):
        super(PrimitiveOrConstructedType, self).__init__(name,
                                                         type_name,
                                                         number,
                                                         flags)
        self.segment = segment
        self.constructed_tag = copy(self.tag)
        self.constructed_tag[0] |= Encoding.CONSTRUCTED

    def set_tag(self, number, flags):
        self.tag = encode_tag(number, flags)
        self.constructed_tag = copy(self.tag)
        self.constructed_tag[0] |= Encoding.CONSTRUCTED
        self.tag_len = len(self.tag)

    def decode(self, data, start_offset, values=None):
        """
        Custom decode logic to handle primitive or constructed types
        Return decoded value and new offset
        """

        offset = start_offset + self.tag_len
        tag = data[start_offset:offset]

        # Validate tag
        if tag == self.tag:
            is_primitive = True
        elif tag == self.constructed_tag:
            is_primitive = False
        elif len(tag) != self.tag_len:
            # Detect out of data
            raise OutOfByteDataError('Ran out of data when reading tag',
                                     offset=start_offset)
        else:
            # Tag mismatch. Return DECODE_FAILED instead of raising DecodeError for performance
            return TAG_MISMATCH, start_offset

        length, offset = decode_length(data, offset, enforce_definite=False)

        if is_primitive:
            end_offset = offset + length
            return self.decode_primitive_contents(data, offset, length), end_offset
        else:
            return self.decode_constructed_contents(data, offset, length)

    def decode_constructed_contents(self, data, offset, length):
        segments = []

        end_offset = None if length is None else offset + length

        while True:
            end_of_data, offset = is_end_of_data(data, offset, end_offset)
            if end_of_data:
                break

            decoded, offset = self.segment.decode(data, offset)
            check_decode_error(self.segment, decoded, data, offset)
            segments.append(decoded)

        return self.decode_constructed_segments(segments), offset

    def decode_primitive_contents(self, data, offset, length):
        raise NotImplementedError('To be implemented by subclasses.')

    def decode_constructed_segments(self, segments):
        raise NotImplementedError('To be implemented by subclasses.')


class StringType(StandardEncodeMixin, PrimitiveOrConstructedType):

    TAG = None
    ENCODING = None

    def __init__(self, name):
        super(StringType, self).__init__(name,
                                         self.__class__.__name__,
                                         self.TAG,
                                         OctetString(name))

    def encode_content(self, data, values=None):
        return data.encode(self.ENCODING)

    def decode_primitive_contents(self, data, offset, length):
        return data[offset:offset + length].decode(self.ENCODING)

    def decode_constructed_segments(self, segments):
        return bytearray().join(segments).decode(self.ENCODING)


class MembersType(StandardEncodeMixin, StandardDecodeMixin, Type):
    indefinite_allowed = True

    def __init__(self, name, tag_name, tag, root_members, additions):
        super(MembersType, self).__init__(name,
                                          tag_name,
                                          tag,
                                          Encoding.CONSTRUCTED)
        self.root_members = root_members
        self.additions = additions

    def set_tag(self, number, flags):
        super(MembersType, self).set_tag(number,
                                         flags | Encoding.CONSTRUCTED)

    def encode_content(self, data, values=None):
        encoded_members = bytearray()

        for member in self.root_members:
            self.encode_member(member, data, encoded_members)


        if self.additions:
            self.encode_additions(data, encoded_members)

        return encoded_members

    def encode_additions(self, data, encoded_members):
        try:
            for addition in self.additions:
                encoded_addition = bytearray()

                if isinstance(addition, list):
                    for member in addition:
                        self.encode_member(member, data, encoded_addition)
                else:
                    self.encode_member(addition,
                                       data,
                                       encoded_addition)

                encoded_members.extend(encoded_addition)
        except EncodeError:
            pass

    def encode_member(self, member, data, encoded_members):
        name = member.name

        if name in data:
            value = data[name]
            try:
                if isinstance(member, AnyDefinedBy):
                    member.encode(value, encoded_members, data)
                elif not member.is_default(value):
                    member.encode(value, encoded_members)
            except ErrorWithLocation as e:
                # Add member location
                e.add_location(member)
                raise e
        elif member.optional:
            pass
        elif not member.has_default():
            raise EncodeError("{} member '{}' not found in {}.".format(
                self.__class__.__name__,
                name,
                data))

    def decode_content(self, data, offset, length):

        end_offset = None if length is None else offset + length

        values = OrderedDict()

        offset, out_of_data = self.decode_members(self.root_members, data, values, offset, end_offset)

        # Decode additions (even if out of data already, so defaults can be added)
        if self.additions:
            offset, out_of_data = self.decode_members(flatten(self.additions), data, values, offset, end_offset,
                                                      ignore_missing=True)

        if out_of_data:
            return values, offset

        if end_offset is None:
            raise NoEndOfContentsTagError('Could not find end-of-contents tag for indefinite length field.',
                                          offset=offset)
        else:
            # Extra data is allowed in cases of versioned additions
            return values, end_offset

    def decode_members(self, members, data, values, offset, end_offset, ignore_missing=False):
        """
        Decode values for members from data starting from offset
        Supports member data encoded in different order than members specified
        :param list members: List of member types
        :param bytearray data:
        :param dict values:
        :param int offset:
        :param int end_offset: End offset of member data (None if indefinite length field)
        :param bool ignore_missing: Whether to not raise DecodeError for missing mandatory fields with no defaults
        :return:
        """
        # Decode member values from data
        remaining_members = members
        # Outer loop to enable decoding members out of order
        while True:
            undecoded_members = []
            decode_success = False  # Whether at least one member was successfully decoded

            out_of_data, offset = is_end_of_data(data, offset, end_offset)
            # Attempt to decode remaining members. If they are encoded in same order, should decode all in one loop
            # Otherwise will require multiple iterations of outer loop
            for member in remaining_members:
                # Dont attempt decode if already out of data, just add member to list of undecoded
                if out_of_data:
                    undecoded_members.append(member)
                    continue

                # Attempt decode
                try:
                    value, offset = member.decode(data, offset, values=values)
                except ErrorWithLocation as e:
                    # Add member location
                    e.add_location(member)
                    raise e

                if value == TAG_MISMATCH:
                    undecoded_members.append(member)
                else:
                    decode_success = True
                    values[member.name] = value

                # Detect end of data
                out_of_data, offset = is_end_of_data(data, offset, end_offset)

            remaining_members = undecoded_members
            if out_of_data:
                break

            if not decode_success:
                # No members are able to decode data, exit loop
                break

        # Handle remaining members that there is no data for
        # (will raise error if member is not optional and has no default)
        for member in remaining_members:
            if member.optional:
                continue

            if member.has_default():
                values[member.name] = member.get_default()
            elif ignore_missing:
                break
            elif out_of_data:
                raise MissingMandatoryFieldError(member, offset)
            else:
                raise DecodeTagError(member, data, offset, location=member)
        return offset, out_of_data

    def __repr__(self):
        return '{}({}, [{}])'.format(
            self.__class__.__name__,
            self.name,
            ', '.join([repr(member) for member in self.root_members]))


class ArrayType(StandardEncodeMixin, StandardDecodeMixin, Type):
    indefinite_allowed = True

    def __init__(self, name, tag_name, tag, element_type):
        super(ArrayType, self).__init__(name,
                                        tag_name,
                                        tag,
                                        Encoding.CONSTRUCTED)
        self.element_type = element_type

    def set_tag(self, number, flags):
        super(ArrayType, self).set_tag(number,
                                       flags | Encoding.CONSTRUCTED)

    def encode_content(self, data, values=None):
        encoded_elements = bytearray()

        for entry in data:
            self.element_type.encode(entry, encoded_elements)

        return encoded_elements

    def decode_content(self, data, offset, length):

        decoded = []
        start_offset = offset
        # Loop through data until length exceeded or end-of-contents tag reached.
        while True:
            if length is None:
                # Find end of indefinite sequence.
                if detect_end_of_contents_tag(data, offset):
                    offset += 2
                    break
            elif (offset - start_offset) >= length:
                # End of definite length sequence.
                break
            decoded_element, offset = self.element_type.decode(data, offset)
            # Invalid Tag
            check_decode_error(self.element_type, decoded_element, data, offset)
            decoded.append(decoded_element)

        return decoded, offset

    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__,
                                   self.name,
                                   self.element_type)


class Boolean(StandardEncodeMixin, StandardDecodeMixin, Type):

    def __init__(self, name):
        super(Boolean, self).__init__(name,
                                      'BOOLEAN',
                                      Tag.BOOLEAN)

    def encode_content(self, data, values=None):
        return bytearray([0xff * data])

    def decode_content(self, data, offset, length):

        if length != 1:
            raise DecodeError(
                'Expected BOOLEAN contents length 1, but '
                'got {}.'.format(length), offset=offset-1)

        return bool(data[offset]), offset + length


class Integer(StandardEncodeMixin, StandardDecodeMixin, Type):

    def __init__(self, name):
        super(Integer, self).__init__(name,
                                      'INTEGER',
                                      Tag.INTEGER)

    def encode_content(self, data, values=None):
        return encode_signed_integer(data)

    def decode_content(self, data, offset, length):
        end_offset = offset + length
        return int.from_bytes(data[offset:end_offset], byteorder='big', signed=True), end_offset


class Real(StandardEncodeMixin, StandardDecodeMixin, Type):

    def __init__(self, name):
        super(Real, self).__init__(name, 'REAL', Tag.REAL)

    def encode_content(self, data, values=None):
        return encode_real(data)

    def decode_content(self, data, offset, length):
        end_offset = offset + length
        decoded = decode_real(data[offset:end_offset])

        return decoded, end_offset


class Null(StandardDecodeMixin, Type):

    def __init__(self, name):
        super(Null, self).__init__(name, 'NULL', Tag.NULL)

    def is_default(self, value):
        return False

    def encode(self, _, encoded):
        encoded.extend(self.tag)
        encoded.append(0)

    def decode_content(self, data, offset, length):
        return None, offset


class BitString(StandardEncodeMixin, PrimitiveOrConstructedType):

    def __init__(self, name, has_named_bits):
        super(BitString, self).__init__(name,
                                        'BIT STRING',
                                        Tag.BIT_STRING,
                                        self)
        self.has_named_bits = has_named_bits

    def is_default(self, value):
        if self.default is None:
            return False

        clean_value = clean_bit_string_value(value,
                                             self.has_named_bits)
        clean_default = clean_bit_string_value(self.default,
                                               self.has_named_bits)

        return clean_value == clean_default

    def encode_content(self, data, values=None):
        number_of_bytes, number_of_rest_bits = divmod(data[1], 8)
        data = bytearray(data[0])

        if number_of_rest_bits == 0:
            data = data[:number_of_bytes]
            number_of_unused_bits = 0
        else:
            last_byte = data[number_of_bytes]
            last_byte &= ((0xff >> number_of_rest_bits) ^ 0xff)
            data = data[:number_of_bytes]
            data.append(last_byte)
            number_of_unused_bits = (8 - number_of_rest_bits)

        return bytearray([number_of_unused_bits]) + data

    def decode_primitive_contents(self, data, offset, length):
        length -= 1
        number_of_bits = 8 * length - data[offset]
        offset += 1

        return (data[offset:offset + length], number_of_bits)

    def decode_constructed_segments(self, segments):
        decoded = bytearray()
        number_of_bits = 0

        for data, length in segments:
            decoded.extend(data)
            number_of_bits += length

        return (bytes(decoded), number_of_bits)


class OctetString(StandardEncodeMixin, PrimitiveOrConstructedType):

    def __init__(self, name):
        super(OctetString, self).__init__(name,
                                          'OCTET STRING',
                                          Tag.OCTET_STRING,
                                          self)

    def encode_content(self, data, values=None):
        return data

    def decode_primitive_contents(self, data, offset, length):
        return bytes(data[offset:offset + length])

    def decode_constructed_segments(self, segments):
        return bytes().join(segments)


class ObjectIdentifier(StandardEncodeMixin, StandardDecodeMixin, Type):

    def __init__(self, name):
        super(ObjectIdentifier, self).__init__(name,
                                               'OBJECT IDENTIFIER',
                                               Tag.OBJECT_IDENTIFIER)

    def encode_content(self, data, values=None):
        return encode_object_identifier(data)

    def decode_content(self, data, offset, length):

        end_offset = offset + length
        decoded = decode_object_identifier(data, offset, end_offset)

        return decoded, end_offset


class Enumerated(StandardEncodeMixin, StandardDecodeMixin, Type):

    def __init__(self, name, values, numeric):
        super(Enumerated, self).__init__(name,
                                         'ENUMERATED',
                                         Tag.ENUMERATED)
        if numeric:
            self.value_to_data = {k: k for k in enum_values_as_dict(values)}
            self.data_to_value = self.value_to_data
        else:
            self.value_to_data = enum_values_as_dict(values)
            self.data_to_value = {v: k for k, v in self.value_to_data.items()}

        self.has_extension_marker = (EXTENSION_MARKER in values)

    def format_names(self):
        return format_or(sorted(list(self.value_to_data.values())))

    def format_values(self):
        return format_or(sorted(list(self.value_to_data)))

    def encode_content(self, data, values=None):
        try:
            value = self.data_to_value[data]
        except KeyError:
            raise EncodeError(
                "Expected enumeration value {}, but got '{}'.".format(
                    self.format_names(),
                    data))

        return encode_signed_integer(value)

    def decode_content(self, data, offset, length):

        end_offset = offset + length
        value = int.from_bytes(data[offset:end_offset], byteorder='big', signed=True)

        if value in self.value_to_data:
            return self.value_to_data[value], end_offset
        elif self.has_extension_marker:
            return None, end_offset
        else:
            raise DecodeError(
                'Expected enumeration value {}, but got {}.'.format(
                    self.format_values(),
                    value), offset=offset)


class Sequence(MembersType):

    def __init__(self, name, root_members, additions):
        super(Sequence, self).__init__(name,
                                       'SEQUENCE',
                                       Tag.SEQUENCE,
                                       root_members,
                                       additions)


class SequenceOf(ArrayType):

    def __init__(self, name, element_type):
        super(SequenceOf, self).__init__(name,
                                         'SEQUENCE OF',
                                         Tag.SEQUENCE,
                                         element_type)


class Set(MembersType):

    def __init__(self, name, root_members, additions):
        super(Set, self).__init__(name,
                                  'SET',
                                  Tag.SET,
                                  root_members,
                                  additions)


class SetOf(ArrayType):

    def __init__(self, name, element_type):
        super(SetOf, self).__init__(name,
                                    'SET OF',
                                    Tag.SET,
                                    element_type)


class Choice(Type):

    def __init__(self, name, root_members, additions):
        super(Choice, self).__init__(name, 'CHOICE', None)
        members = root_members

        if additions is not None:
            for addition in additions:
                if isinstance(addition, list):
                    members += addition
                else:
                    members.append(addition)

            self.has_extension_marker = True
        else:
            self.has_extension_marker = False

        self.members = members
        self.name_to_member = {member.name: member for member in self.members}
        self.tag_to_member = {}
        self.add_tags(self.members)

    def add_tags(self, members):
        for member in members:
            tags = self.get_member_tags(member)

            for tag in tags:
                self.tag_to_member[tag] = member

    def get_member_tags(self, member):
        tags = []

        if isinstance(member, Choice):
            tags = self.get_choice_tags(member)
        elif isinstance(member, Recursive):
            if member.inner is None:
                member.choice_parents.append(self)
            else:
                tags = self.get_member_tags(member.inner)
        else:
            tags.append(bytes(member.tag))

            if hasattr(member, 'constructed_tag'):
                tags.append(bytes(member.constructed_tag))

        return tags

    def get_choice_tags(self, choice):
        tags = []

        for member in choice.members:
            tags.extend(self.get_member_tags(member))

        return tags

    def format_tag(self):
        return [format_bytes(tag) for tag in self.tag_to_member]

    def format_names(self):
        return format_or(sorted([member.name for member in self.members]))

    def encode(self, data, encoded, values=None):
        try:
            member = self.name_to_member[data[0]]
        except KeyError:
            raise EncodeError(
                "Expected choice {}, but got '{}'.".format(
                    self.format_names(),
                    data[0]))
        try:
            member.encode(data[1], encoded)
        except ErrorWithLocation as e:
            # Add member location
            e.add_location(member)
            raise e

    def decode(self, data, offset, values=None):
        tag = bytes(read_tag(data, offset))

        if tag in self.tag_to_member:
            member = self.tag_to_member[tag]
        elif self.has_extension_marker:
            offset = skip_tag_length_contents(data, offset)

            return (None, None), offset
        else:
            return TAG_MISMATCH, offset
        try:
            decoded, offset = member.decode(data, offset)
        except ErrorWithLocation as e:
            # Add member location
            e.add_location(member)
            raise e

        return (member.name, decoded), offset

    def __repr__(self):
        return 'Choice({}, [{}])'.format(
            self.name,
            ', '.join([repr(member) for member in self.members]))


class UTF8String(StringType):

    TAG = Tag.UTF8_STRING
    ENCODING = 'utf-8'


class NumericString(StringType):

    TAG = Tag.NUMERIC_STRING
    ENCODING = 'ascii'


class PrintableString(StringType):

    TAG = Tag.PRINTABLE_STRING
    ENCODING = 'ascii'


class IA5String(StringType):

    TAG = Tag.IA5_STRING
    ENCODING = 'ascii'


class VisibleString(StringType):

    TAG = Tag.VISIBLE_STRING
    ENCODING = 'ascii'


class GeneralString(StringType):

    TAG = Tag.GENERAL_STRING
    ENCODING = 'latin-1'


class BMPString(StringType):

    TAG = Tag.BMP_STRING
    ENCODING = 'utf-16-be'


class GraphicString(StringType):

    TAG = Tag.GRAPHIC_STRING
    ENCODING = 'latin-1'


class UniversalString(StringType):

    TAG = Tag.UNIVERSAL_STRING
    ENCODING = 'utf-32-be'


class TeletexString(StringType):

    TAG = Tag.T61_STRING
    ENCODING = 'iso-8859-1'


class ObjectDescriptor(GraphicString):

    TAG = Tag.OBJECT_DESCRIPTOR


class UTCTime(StandardEncodeMixin, StandardDecodeMixin, Type):

    def __init__(self, name):
        super(UTCTime, self).__init__(name,
                                      'UTCTime',
                                      Tag.UTC_TIME)

    def encode_content(self, data, values=None):
        return utc_time_from_datetime(data).encode('ascii')

    def decode_content(self, data, offset, length):

        end_offset = offset + length
        decoded = data[offset:end_offset].decode('ascii')

        return utc_time_to_datetime(decoded), end_offset


class GeneralizedTime(StandardEncodeMixin, StandardDecodeMixin, Type):

    def __init__(self, name):
        super(GeneralizedTime, self).__init__(name,
                                              'GeneralizedTime',
                                              Tag.GENERALIZED_TIME)

    def encode_content(self, data, values=None):
        return generalized_time_from_datetime(data).encode('ascii')

    def decode_content(self, data, offset, length):

        end_offset = offset + length
        decoded = data[offset:end_offset].decode('ascii')

        return generalized_time_to_datetime(decoded), end_offset


class Date(StandardEncodeMixin, StandardDecodeMixin, Type):

    def __init__(self, name):
        super(Date, self).__init__(name, 'DATE', Tag.DATE)

    def encode_content(self, data, values=None):
        return str(data).replace('-', '').encode('ascii')

    def decode_content(self, data, offset, length):

        end_offset = offset + length
        decoded = data[offset:end_offset].decode('ascii')
        decoded = datetime.date(*time.strptime(decoded, '%Y%m%d')[:3])

        return decoded, end_offset


class TimeOfDay(StandardEncodeMixin, StandardDecodeMixin, Type):

    def __init__(self, name):
        super(TimeOfDay, self).__init__(name,
                                        'TIME-OF-DAY',
                                        Tag.TIME_OF_DAY)

    def encode_content(self, data, values=None):
        return str(data).replace(':', '').encode('ascii')

    def decode_content(self, data, offset, length):

        end_offset = offset + length
        decoded = data[offset:end_offset].decode('ascii')
        decoded = datetime.time(*time.strptime(decoded, '%H%M%S')[3:6])

        return decoded, end_offset


class DateTime(StandardEncodeMixin, StandardDecodeMixin, Type):

    def __init__(self, name):
        super(DateTime, self).__init__(name,
                                       'DATE-TIME',
                                       Tag.DATE_TIME)

    def encode_content(self, data, values=None):
        return '{:04d}{:02d}{:02d}{:02d}{:02d}{:02d}'.format(*data.timetuple()).encode('ascii')

    def decode_content(self, data, offset, length):

        end_offset = offset + length
        decoded = data[offset:end_offset].decode('ascii')
        decoded = datetime.datetime(*time.strptime(decoded, '%Y%m%d%H%M%S')[:6])

        return decoded, end_offset


class Any(Type):

    def __init__(self, name):
        super(Any, self).__init__(name, 'ANY', None)

    def encode(self, data, encoded):
        encoded.extend(data)

    def decode(self, data, offset, values=None):
        start = offset
        offset = skip_tag(data, offset)
        length, offset = decode_length(data, offset)
        end_offset = offset + length

        return data[start:end_offset], end_offset


class AnyDefinedBy(Type):

    def __init__(self, name, type_member, choices):
        super(AnyDefinedBy, self).__init__(name,
                                           'ANY DEFINED BY',
                                           None,
                                           None)
        self.type_member = type_member
        self.choices = choices

    def encode(self, data, encoded, values):
        if self.choices:
            try:
                self.choices[values[self.type_member]].encode(data, encoded)
            except KeyError:
                raise EncodeError('Bad AnyDefinedBy choice {}.'.format(
                    values[self.type_member]))
        else:
            encoded.extend(data)

    def decode(self, data, offset, values):
        """

        :param data:
        :param int offset: Byte offset in ASN1 data
        :param dict values: Dictionary of already decoded values in containing type
        :return:
        """
        if self.choices:
            try:
                return self.choices[values[self.type_member]].decode(data,
                                                                     offset)
            except KeyError:
                raise DecodeError('Bad AnyDefinedBy choice {}.'.format(
                    values[self.type_member]),
                    offset=offset)
        else:
            start = offset
            offset = skip_tag(data, offset)
            length, offset = decode_length(data, offset)
            end_offset = offset + length

            return data[start:end_offset], end_offset


class ExplicitTag(StandardEncodeMixin, StandardDecodeMixin, Type):
    # no_error_location = True
    indefinite_allowed = True

    def __init__(self, name, inner):
        super(ExplicitTag, self).__init__(name, 'ExplicitTag', None)
        self.inner = inner

    def set_default(self, value):
        self.inner.set_default(value)

    def get_default(self):
        return self.inner.get_default()

    def is_default(self, value):
        return self.inner.is_default(value)

    def has_default(self):
        return self.inner.has_default()

    def set_tag(self, number, flags):
        super(ExplicitTag, self).set_tag(number,
                                         flags | Encoding.CONSTRUCTED)

    def encode_content(self, data, values=None):
        encoded_inner = bytearray()
        self.inner.encode(data, encoded_inner)
        return encoded_inner

    def decode_content(self, data, offset, length):

        values, end_offset = self.inner.decode(data, offset)

        check_decode_error(self.inner, values, data, offset)

        # Verify End of Contents tag exists for Indefinite field
        if length is None:
            if not detect_end_of_contents_tag(data, end_offset):
                raise NoEndOfContentsTagError('Expected end-of-contents tag.',
                                              offset=end_offset,
                                              location=self)
            end_offset += 2

        return values, end_offset


class Recursive(compiler.Recursive, Type):

    def __init__(self, name, type_name, module_name):
        super(Recursive, self).__init__(name, 'RECURSIVE', None)
        self.type_name = type_name
        self.module_name = module_name
        self.tag_number = None
        self.tag_flags = None
        self.inner = None
        self.choice_parents = []

    def set_tag(self, number, flags):
        self.tag_number = number
        self.tag_flags = flags

    def set_inner_type(self, inner):
        self.inner = copy(inner)

        if self.tag_number is not None:
            self.inner.set_tag(self.tag_number, self.tag_flags)

        for choice_parent in self.choice_parents:
            choice_parent.add_tags([self])

    def encode(self, data, encoded, values=None):
        self.inner.encode(data, encoded)

    def decode(self, data, offset, values=None):
        return self.inner.decode(data, offset)


class CompiledType(compiler.CompiledType):

    def encode(self, data):
        encoded = bytearray()
        try:
            self._type.encode(data, encoded)
        except ErrorWithLocation as e:
            # Add member location
            e.add_location(self._type)
            raise e

        return encoded

    def decode(self, data):
        return self.decode_with_length(data)[0]

    def decode_with_length(self, data):
        """
        Decode and return decoded values as well as length of binary data decoded
        :param data:
        :return:
        """
        try:
            decoded, offset = self._type.decode(bytearray(data), 0)
            # Raise DecodeError
            check_decode_error(self._type, decoded, data, offset)
        except ErrorWithLocation as e:
            # Add member location
            e.add_location(self._type)
            raise e
        return decoded, offset


def get_tag_no_encoding(member):
    value = (member.tag[0] & ~Encoding.CONSTRUCTED)

    return bytearray([value]) + member.tag[1:]


class Compiler(compiler.Compiler):

    def process_type(self, type_name, type_descriptor, module_name):
        compiled_type = self.compile_type(type_name,
                                          type_descriptor,
                                          module_name)

        return CompiledType(compiled_type)

    def compile_implicit_type(self, name, type_descriptor, module_name):
        type_name = type_descriptor['type']

        if type_name == 'SEQUENCE':
            compiled = Sequence(
                name,
                *self.compile_members(type_descriptor['members'],
                                      module_name))
        elif type_name == 'SEQUENCE OF':
            compiled = SequenceOf(name,
                                  self.compile_type('',
                                                    type_descriptor['element'],
                                                    module_name))
        elif type_name == 'SET':
            compiled = Set(
                name,
                *self.compile_members(type_descriptor['members'],
                                      module_name,
                                      sort_by_tag=True))
        elif type_name == 'SET OF':
            compiled = SetOf(name,
                             self.compile_type('',
                                               type_descriptor['element'],
                                               module_name))
        elif type_name == 'CHOICE':
            compiled = Choice(
                name,
                *self.compile_members(type_descriptor['members'],
                                      module_name))
        elif type_name == 'INTEGER':
            compiled = Integer(name)
        elif type_name == 'REAL':
            compiled = Real(name)
        elif type_name == 'ENUMERATED':
            compiled = Enumerated(name,
                                  self.get_enum_values(type_descriptor,
                                                       module_name),
                                  self._numeric_enums)
        elif type_name == 'BOOLEAN':
            compiled = Boolean(name)
        elif type_name == 'OBJECT IDENTIFIER':
            compiled = ObjectIdentifier(name)
        elif type_name == 'OCTET STRING':
            compiled = OctetString(name)
        elif type_name == 'TeletexString':
            compiled = TeletexString(name)
        elif type_name == 'NumericString':
            compiled = NumericString(name)
        elif type_name == 'PrintableString':
            compiled = PrintableString(name)
        elif type_name == 'IA5String':
            compiled = IA5String(name)
        elif type_name == 'VisibleString':
            compiled = VisibleString(name)
        elif type_name == 'GeneralString':
            compiled = GeneralString(name)
        elif type_name == 'UTF8String':
            compiled = UTF8String(name)
        elif type_name == 'BMPString':
            compiled = BMPString(name)
        elif type_name == 'GraphicString':
            compiled = GraphicString(name)
        elif type_name == 'UTCTime':
            compiled = UTCTime(name)
        elif type_name == 'UniversalString':
            compiled = UniversalString(name)
        elif type_name == 'GeneralizedTime':
            compiled = GeneralizedTime(name)
        elif type_name == 'DATE':
            compiled = Date(name)
        elif type_name == 'TIME-OF-DAY':
            compiled = TimeOfDay(name)
        elif type_name == 'DATE-TIME':
            compiled = DateTime(name)
        elif type_name == 'BIT STRING':
            has_named_bits = ('named-bits' in type_descriptor)
            compiled = BitString(name, has_named_bits)
        elif type_name == 'ANY':
            compiled = Any(name)
        elif type_name == 'ANY DEFINED BY':
            choices = {}

            for key, value in type_descriptor['choices'].items():
                choices[key] = self.compile_type(key,
                                                 value,
                                                 module_name)

            compiled = AnyDefinedBy(name,
                                    type_descriptor['value'],
                                    choices)
        elif type_name == 'NULL':
            compiled = Null(name)
        elif type_name == 'EXTERNAL':
            compiled = Sequence(
                name,
                *self.compile_members(self.external_type_descriptor()['members'],
                                      module_name))
            compiled.set_tag(Tag.EXTERNAL, 0)
        elif type_name == 'ObjectDescriptor':
            compiled = ObjectDescriptor(name)
        else:
            if type_name in self.types_backtrace:
                compiled = Recursive(name,
                                     type_name,
                                     module_name)
                self.recursive_types.append(compiled)
            else:
                compiled = self.compile_user_type(name,
                                                  type_name,
                                                  module_name)

        return compiled

    def compile_type(self, name, type_descriptor, module_name):
        module_name = self.get_module_name(type_descriptor, module_name)
        compiled = self.compile_implicit_type(name,
                                              type_descriptor,
                                              module_name)

        if self.is_explicit_tag(type_descriptor):
            compiled = ExplicitTag(name, compiled)

        # Set any given tag.
        if 'tag' in type_descriptor:
            compiled = self.copy(compiled)
            tag = type_descriptor['tag']
            class_ = tag.get('class', None)

            if class_ == 'APPLICATION':
                flags = Class.APPLICATION
            elif class_ == 'PRIVATE':
                flags = Class.PRIVATE
            elif class_ == 'UNIVERSAL':
                flags = 0
            else:
                flags = Class.CONTEXT_SPECIFIC

            compiled.set_tag(tag['number'], flags)

        return compiled

    def compile_members(self,
                        members,
                        module_name,
                        sort_by_tag=False):
        compiled_members = []
        in_extension = False
        additions = None

        for member in members:
            if member == EXTENSION_MARKER:
                in_extension = not in_extension

                if in_extension:
                    additions = []
            elif in_extension:
                self.compile_extension_member(member,
                                              module_name,
                                              additions)
            else:
                self.compile_root_member(member,
                                         module_name,
                                         compiled_members)

        if sort_by_tag:
            compiled_members = sorted(compiled_members, key=get_tag_no_encoding)

        return compiled_members, additions

    def compile_extension_member(self,
                                 member,
                                 module_name,
                                 additions):
        if isinstance(member, list):
            compiled_group = []

            for memb in member:
                compiled_member = self.compile_member(memb,
                                                      module_name)
                compiled_group.append(compiled_member)

            additions.append(compiled_group)
        else:
            compiled_member = self.compile_member(member,
                                                  module_name)
            additions.append(compiled_member)


def compile_dict(specification, numeric_enums=False):
    return Compiler(specification, numeric_enums).process()


def decode_full_length(data):
    """
    Get total byte length of ASN1 element (tag + contents length)
    :param data:
    :return:
    """
    try:
        return skip_tag_length_contents(bytearray(data), 0)
    except MissingDataError as e:
        return e.offset + e.expected_length
    except OutOfByteDataError:
        return None


