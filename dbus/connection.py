# Copyright (C) 2007 Collabora Ltd. <http://www.collabora.co.uk/>
#
# Licensed under the Academic Free License version 2.1
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2.1 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

__all__ = ('Connection', 'SignalMatch')
__docformat__ = 'reStructuredText'

import logging
try:
    import thread
except ImportError:
    import dummy_thread as thread
import weakref

from _dbus_bindings import Connection as _Connection, ErrorMessage, \
                           MethodCallMessage, MethodReturnMessage, \
                           DBusException, LOCAL_PATH, LOCAL_IFACE, \
                           validate_interface_name, validate_member_name,\
                           validate_bus_name, validate_object_path,\
                           validate_error_name, \
                           HANDLER_RESULT_NOT_YET_HANDLED, \
                           UTF8String, SignalMessage
from dbus.proxies import ProxyObject


_logger = logging.getLogger('dbus.connection')


def _noop(*args, **kwargs):
    pass


class SignalMatch(object):
    __slots__ = ('sender_unique', '_member', '_interface', '_sender',
                 '_path', '_handler', '_args_match', '_rule',
                 '_utf8_strings', '_byte_arrays', '_conn_weakref',
                 '_destination_keyword', '_interface_keyword',
                 '_message_keyword', '_member_keyword',
                 '_sender_keyword', '_path_keyword', '_int_args_match')

    def __init__(self, conn, sender, object_path, dbus_interface,
                 member, handler, utf8_strings=False, byte_arrays=False,
                 sender_keyword=None, path_keyword=None,
                 interface_keyword=None, member_keyword=None,
                 message_keyword=None, destination_keyword=None,
                 **kwargs):
        if member is not None:
            validate_member_name(member)
        if dbus_interface is not None:
            validate_interface_name(dbus_interface)
        if sender is not None:
            validate_bus_name(sender)
        if object_path is not None:
            validate_object_path(object_path)

        self._conn_weakref = weakref.ref(conn)
        self._sender = sender
        self._interface = dbus_interface
        self._member = member
        self._path = object_path
        self._handler = handler

        # if the connection is actually a bus, it's responsible for changing
        # this
        self.sender_unique = sender

        self._utf8_strings = utf8_strings
        self._byte_arrays = byte_arrays
        self._sender_keyword = sender_keyword
        self._path_keyword = path_keyword
        self._member_keyword = member_keyword
        self._interface_keyword = interface_keyword
        self._message_keyword = message_keyword
        self._destination_keyword = destination_keyword

        self._args_match = kwargs
        if not kwargs:
            self._int_args_match = None
        else:
            self._int_args_match = {}
            for kwarg in kwargs:
                if not kwarg.startswith('arg'):
                    raise TypeError('SignalMatch: unknown keyword argument %s'
                                    % kwarg)
                try:
                    index = int(kwarg[3:])
                except ValueError:
                    raise TypeError('SignalMatch: unknown keyword argument %s'
                                    % kwarg)
                if index < 0 or index > 63:
                    raise TypeError('SignalMatch: arg match index must be in '
                                    'range(64), not %d' % index)
                self._int_args_match[index] = kwargs[kwarg]

        # we're probably going to have to calculate the match rule for
        # the Bus's benefit, so this constructor might as well do the work
        rule = ["type='signal'"]
        if self._sender is not None:
            rule.append("sender='%s'" % self._sender)
        if self._path is not None:
            rule.append("path='%s'" % self._path)
        if self._interface is not None:
            rule.append("interface='%s'" % self._interface)
        if self._member is not None:
            rule.append("member='%s'" % self._member)
        for kwarg, value in kwargs.iteritems():
            rule.append("%s='%s'" % (kwarg, value))

        self._rule = ','.join(rule)

    sender = property(lambda self: self._sender)

    def __str__(self):
        return self._rule

    def __repr__(self):
        return ('<%s at %x "%s" on conn %r>'
                % (self.__class__, id(self), self._rule, self._conn_weakref()))

    def matches_removal_spec(self, sender, object_path,
                             dbus_interface, member, handler, **kwargs):
        if handler not in (None, self._handler):
            return False
        if sender != self._sender:
            return False
        if object_path != self._path:
            return False
        if dbus_interface != self._interface:
            return False
        if member != self._member:
            return False
        if kwargs != self._args_match:
            return False
        return True

    def maybe_handle_message(self, message):
        args = None

        # these haven't been checked yet by the match tree
        if self.sender_unique not in (None, message.get_sender()):
            return False
        if self._int_args_match is not None:
            # extracting args with utf8_strings and byte_arrays is less work
            args = message.get_args_list(utf8_strings=True, byte_arrays=True)
            for index, value in self._int_args_match.iteritems():
                if (index >= len(args)
                    or not isinstance(args[index], UTF8String)
                    or args[index] != value):
                    return False

        # these have likely already been checked by the match tree
        if self._member not in (None, message.get_member()):
            return False
        if self._interface not in (None, message.get_interface()):
            return False
        if self._path not in (None, message.get_path()):
            return False

        try:
            # minor optimization: if we already extracted the args with the
            # right calling convention to do the args match, don't bother
            # doing so again
            if args is None or not self._utf8_strings or not self._byte_arrays:
                args = message.get_args_list(utf8_strings=self._utf8_strings,
                                             byte_arrays=self._byte_arrays)
            kwargs = {}
            if self._sender_keyword is not None:
                kwargs[self._sender_keyword] = message.get_sender()
            if self._destination_keyword is not None:
                kwargs[self._destination_keyword] = message.get_destination()
            if self._path_keyword is not None:
                kwargs[self._path_keyword] = message.get_path()
            if self._member_keyword is not None:
                kwargs[self._member_keyword] = message.get_member()
            if self._interface_keyword is not None:
                kwargs[self._interface_keyword] = message.get_interface()
            if self._message_keyword is not None:
                kwargs[self._message_keyword] = message
            self._handler(*args, **kwargs)
        except:
            # basicConfig is a no-op if logging is already configured
            logging.basicConfig()
            _logger.error('Exception in handler for D-Bus signal:', exc_info=1)

        return True

    def remove(self):
        conn = self._conn_weakref()
        # do nothing if the connection has already vanished
        if conn is not None:
            conn.remove_signal_receiver(self, self._member,
                                        self._interface, self._sender,
                                        self._path,
                                        **self._args_match)


class Connection(_Connection):
    """A connection to another application. In this base class there is
    assumed to be no bus daemon.
    """

    ProxyObjectClass = ProxyObject

    def __init__(self, *args, **kwargs):
        super(Connection, self).__init__(*args, **kwargs)

        # this if-block is needed because shared bus connections can be
        # __init__'ed more than once
        if not hasattr(self, '_dbus_Connection_initialized'):
            self._dbus_Connection_initialized = 1

            self._signal_recipients_by_object_path = {}
            """Map from object path to dict mapping dbus_interface to dict
            mapping member to list of SignalMatch objects."""

            self._signals_lock = thread.allocate_lock()
            """Lock used to protect signal data structures if doing two
            removals at the same time (everything else is atomic, thanks to
            the GIL)"""

            self.add_message_filter(self.__class__._signal_func)

    def activate_name_owner(self, bus_name):
        """Return the unique name for the given bus name, activating it
        if necessary and possible.

        If the name is already unique or this connection is not to a
        bus daemon, just return it.

        :Returns: a bus name. If the given `bus_name` exists, the returned
            name identifies its current owner; otherwise the returned name
            does not exist.
        :Raises DBusException: if the implementation has failed
            to activate the given bus name.
        """
        return bus_name

    def get_object(self, named_service, object_path, introspect=True):
        """Return a local proxy for the given remote object.

        Method calls on the proxy are translated into method calls on the
        remote object.

        :Parameters:
            `named_service` : str
                A bus name (either the unique name or a well-known name)
                of the application owning the object
            `object_path` : str
                The object path of the desired object
            `introspect` : bool
                If true (default), attempt to introspect the remote
                object to find out supported methods and their signatures

        :Returns: a `dbus.proxies.ProxyObject`
        """
        return self.ProxyObjectClass(self, named_service, object_path,
                                     introspect=introspect)

    def add_signal_receiver(self, handler_function,
                                  signal_name=None,
                                  dbus_interface=None,
                                  named_service=None,
                                  path=None,
                                  **keywords):
        """Arrange for the given function to be called when a signal matching
        the parameters is received.

        :Parameters:
            `handler_function` : callable
                The function to be called. Its positional arguments will
                be the arguments of the signal. By default it will receive
                no keyword arguments, but see the description of
                the optional keyword arguments below.
            `signal_name` : str
                The signal name; None (the default) matches all names
            `dbus_interface` : str
                The D-Bus interface name with which to qualify the signal;
                None (the default) matches all interface names
            `named_service` : str
                A bus name for the sender, which will be resolved to a
                unique name if it is not already; None (the default) matches
                any sender
            `path` : str
                The object path of the object which must have emitted the
                signal; None (the default) matches any object path
        :Keywords:
            `utf8_strings` : bool
                If True, the handler function will receive any string
                arguments as dbus.UTF8String objects (a subclass of str
                guaranteed to be UTF-8). If False (default) it will receive
                any string arguments as dbus.String objects (a subclass of
                unicode).
            `byte_arrays` : bool
                If True, the handler function will receive any byte-array
                arguments as dbus.ByteArray objects (a subclass of str).
                If False (default) it will receive any byte-array
                arguments as a dbus.Array of dbus.Byte (subclasses of:
                a list of ints).
            `sender_keyword` : str
                If not None (the default), the handler function will receive
                the unique name of the sending endpoint as a keyword
                argument with this name.
            `destination_keyword` : str
                If not None (the default), the handler function will receive
                the bus name of the destination (or None if the signal is a
                broadcast, as is usual) as a keyword argument with this name.
            `interface_keyword` : str
                If not None (the default), the handler function will receive
                the signal interface as a keyword argument with this name.
            `member_keyword` : str
                If not None (the default), the handler function will receive
                the signal name as a keyword argument with this name.
            `path_keyword` : str
                If not None (the default), the handler function will receive
                the object-path of the sending object as a keyword argument
                with this name.
            `message_keyword` : str
                If not None (the default), the handler function will receive
                the `dbus.lowlevel.SignalMessage` as a keyword argument with
                this name.
            `arg...` : unicode or UTF-8 str
                If there are additional keyword parameters of the form
                ``arg``\ *n*, match only signals where the *n*\ th argument
                is the value given for that keyword parameter. As of this
                time only string arguments can be matched (in particular,
                object paths and signatures can't).
        """
        self._require_main_loop()

        match = SignalMatch(self, named_service, path, dbus_interface,
                            signal_name, handler_function, **keywords)
        by_interface = self._signal_recipients_by_object_path.setdefault(path,
                                                                         {})
        by_member = by_interface.setdefault(dbus_interface, {})
        matches = by_member.setdefault(signal_name, [])

        # make sure nobody is currently manipulating the list
        self._signals_lock.acquire()
        try:
            matches.append(match)
        finally:
            self._signals_lock.release()
        return match

    def _iter_easy_matches(self, path, dbus_interface, member):
        if path is not None:
            path_keys = (None, path)
        else:
            path_keys = (None,)
        if dbus_interface is not None:
            interface_keys = (None, dbus_interface)
        else:
            interface_keys = (None,)
        if member is not None:
            member_keys = (None, member)
        else:
            member_keys = (None,)

        for path in path_keys:
            by_interface = self._signal_recipients_by_object_path.get(path,
                                                                      None)
            if by_interface is None:
                continue
            for dbus_interface in interface_keys:
                by_member = by_interface.get(dbus_interface, None)
                if by_member is None:
                    continue
                for member in member_keys:
                    matches = by_member.get(member, None)
                    if matches is None:
                        continue
                    for m in matches:
                        yield m

    def remove_signal_receiver(self, handler_or_match,
                               signal_name=None,
                               dbus_interface=None,
                               named_service=None,
                               path=None,
                               **keywords):
        by_interface = self._signal_recipients_by_object_path.get(path, None)
        if by_interface is None:
            return
        by_member = by_interface.get(dbus_interface, None)
        if by_member is None:
            return
        matches = by_member.get(signal_name, None)
        if matches is None:
            return
        self._signals_lock.acquire()
        try:
            new = []
            for match in matches:
                if (handler_or_match is match
                    or match.matches_removal_spec(named_service,
                                                  path,
                                                  dbus_interface,
                                                  signal_name,
                                                  handler_or_match,
                                                  **keywords)):
                    self._clean_up_signal_match(match)
                else:
                    new.append(match)
            by_member[signal_name] = new
        finally:
            self._signals_lock.release()

    def _clean_up_signal_match(self, match):
        # Called with the signals lock held
        pass

    def _signal_func(self, message):
        """D-Bus filter function. Handle signals by dispatching to Python
        callbacks kept in the match-rule tree.
        """

        if not isinstance(message, SignalMessage):
            return HANDLER_RESULT_NOT_YET_HANDLED

        dbus_interface = message.get_interface()
        path = message.get_path()
        signal_name = message.get_member()

        for match in self._iter_easy_matches(path, dbus_interface,
                                             signal_name):
            match.maybe_handle_message(message)
        return HANDLER_RESULT_NOT_YET_HANDLED

    def call_async(self, bus_name, object_path, dbus_interface, method,
                   signature, args, reply_handler, error_handler,
                   timeout=-1.0, utf8_strings=False, byte_arrays=False,
                   require_main_loop=True):
        """Call the given method, asynchronously.

        If the reply_handler is None, successful replies will be ignored.
        If the error_handler is None, failures will be ignored. If both
        are None, the implementation may request that no reply is sent.

        :Returns: The dbus.lowlevel.PendingCall.
        """
        if object_path == LOCAL_PATH:
            raise DBusException('Methods may not be called on the reserved '
                                'path %s' % LOCAL_PATH)
        if dbus_interface == LOCAL_IFACE:
            raise DBusException('Methods may not be called on the reserved '
                                'interface %s' % LOCAL_IFACE)
        # no need to validate other args - MethodCallMessage ctor will do

        get_args_opts = {'utf8_strings': utf8_strings,
                         'byte_arrays': byte_arrays}

        message = MethodCallMessage(destination=bus_name,
                                    path=object_path,
                                    interface=dbus_interface,
                                    method=method)
        # Add the arguments to the function
        try:
            message.append(signature=signature, *args)
        except Exception, e:
            logging.basicConfig()
            _logger.error('Unable to set arguments %r according to '
                          'signature %r: %s: %s',
                          args, signature, e.__class__, e)
            raise

        if reply_handler is None and error_handler is None:
            # we don't care what happens, so just send it
            self.send_message(message)
            return

        if reply_handler is None:
            reply_handler = _noop
        if error_handler is None:
            error_handler = _noop

        def msg_reply_handler(message):
            if isinstance(message, MethodReturnMessage):
                reply_handler(*message.get_args_list(**get_args_opts))
            elif isinstance(message, ErrorMessage):
                args = message.get_args_list()
                # FIXME: should we do something with the rest?
                if len(args) > 0:
                    error_handler(DBusException(args[0]))
                else:
                    error_handler(DBusException())
            else:
                error_handler(TypeError('Unexpected type for reply '
                                        'message: %r' % message))
        return self.send_message_with_reply(message, msg_reply_handler,
                                        timeout/1000.0,
                                        require_main_loop=require_main_loop)

    def call_blocking(self, bus_name, object_path, dbus_interface, method,
                      signature, args, timeout=-1.0, utf8_strings=False,
                      byte_arrays=False):
        """Call the given method, synchronously.
        """
        if object_path == LOCAL_PATH:
            raise DBusException('Methods may not be called on the reserved '
                                'path %s' % LOCAL_PATH)
        if dbus_interface == LOCAL_IFACE:
            raise DBusException('Methods may not be called on the reserved '
                                'interface %s' % LOCAL_IFACE)
        # no need to validate other args - MethodCallMessage ctor will do

        get_args_opts = {'utf8_strings': utf8_strings,
                         'byte_arrays': byte_arrays}

        message = MethodCallMessage(destination=bus_name,
                                    path=object_path,
                                    interface=dbus_interface,
                                    method=method)
        # Add the arguments to the function
        try:
            message.append(signature=signature, *args)
        except Exception, e:
            logging.basicConfig()
            _logger.error('Unable to set arguments %r according to '
                          'signature %r: %s: %s',
                          args, signature, e.__class__, e)
            raise

        # make a blocking call
        reply_message = self.send_message_with_reply_and_block(
            message, timeout)
        args_list = reply_message.get_args_list(**get_args_opts)
        if len(args_list) == 0:
            return None
        elif len(args_list) == 1:
            return args_list[0]
        else:
            return tuple(args_list)