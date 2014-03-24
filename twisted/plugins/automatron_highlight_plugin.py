import cgi
from automatron.controller.controller import IAutomatronClientActions
from automatron.core.event import STOP
from automatron.core.util import parse_user

try:
    import ujson as json
except ImportError:
    import json
import datetime
from twisted.internet import defer
from zope.interface import implements, classProvides
from automatron.backend.command import IAutomatronCommandHandler
from automatron.backend.plugin import IAutomatronPluginFactory
from automatron.controller.client import IAutomatronMessageHandler
import re
from automatron_notify import IAutomatronNotifyHandler


class HighlightPlugin(object):
    classProvides(IAutomatronPluginFactory)
    implements(IAutomatronMessageHandler, IAutomatronCommandHandler)

    name = 'highlight'
    priority = 100

    def __init__(self, controller):
        self.controller = controller

    def _msg(self, server, user, message):
        self.controller.plugins.emit(
            IAutomatronClientActions['message'],
            server,
            user,
            message
        )

    def on_message(self, server, user, channel, message):
        if not message:
            return
        self._on_message(server, user, channel, message)

    @defer.inlineCallbacks
    def _on_message(self, server, user, channel, message):
        config = yield self.controller.config.get_plugin_section(self, server['server'], channel)
        events = {}

        own_username, _ = yield self.controller.config.get_username_by_hostmask(server['server'], user)

        for highlight, highlight_usernames in config.items():
            matches = []
            last = 0
            if highlight.startswith('~'):
                while True:
                    match = re.search(highlight[1:], message[last:])
                    if match is None:
                        break
                    matches.append([last + match.start(), last + match.end()])
                    last += match.end()
            else:
                while True:
                    try:
                        start = last + message[last:].index(highlight)
                    except ValueError:
                        break
                    last = start + len(highlight)
                    matches.append([start, last])

            if not matches:
                continue

            highlight_usernames = [u.encode('utf-8') for u in json.loads(highlight_usernames)]

            for username in highlight_usernames:
                if username == own_username:
                    continue

                if not username in events:
                    events[username] = []
                events[username].extend(matches)

        if not events:  # Early abort
            return

        timestamp = datetime.datetime.now().strftime('%H:%M')
        nickname = parse_user(user)[0]
        for username, matches in events.items():
            # Compress overlapping regions if multiple triggers match this message
            matches = sorted(matches, key=lambda m: m[0])
            matches_compressed = [matches[0]]
            for match in matches[1:]:
                if match[0] < matches_compressed[-1][1]:
                    matches_compressed[-1][1] = max(matches_compressed[-1][1], match[1])
                else:
                    matches_compressed.append(match)

            # Paint matches red
            last = 0
            message_html = ''
            for start, end in matches_compressed:
                message_html += cgi.escape(message[last:start]) + \
                        '<font color="red">' + \
                        cgi.escape(message[start:end]) + \
                        '</font>'
                last = end
            message_html += cgi.escape(message[last:])

            self.controller.plugins.emit(
                IAutomatronNotifyHandler['on_notify'],
                server['server'],
                username,
                'Highlight in %s on %s' % (channel, server['server']),
                '%s <%s> %s' % (timestamp, nickname, message),
                '%s <b>&lt;%s&gt;</b> %s' % (timestamp, cgi.escape(nickname), message_html),
            )

    def on_command(self, server, user, command, args):
        if command != 'highlight':
            return

        return self._on_command(server, user, args)

    @defer.inlineCallbacks
    def _on_command(self, server, user, args):
        if len(args) != 2:
            self._msg(server['server'], user, 'Syntax: highlight <channel> <highlight>')
            self._msg(server['server'], user, 'If highlight starts with a ~ it will be interpreted as a regular '
                                   'expression.')
            defer.returnValue(STOP)

        channel, highlight = args

        if not (yield self.controller.config.has_permission(server['server'], channel, user, 'highlight')):
            self._msg(server['server'], user, 'You\'re not authorized to set up highlights.')
            defer.returnValue(STOP)

        username, _ = yield self.controller.config.get_username_by_hostmask(server['server'], user)

        highlight_usernames, _ = yield self.controller.config.get_plugin_value(
            self,
            server['server'],
            channel,
            highlight
        )

        if highlight_usernames is not None:
            highlight_usernames = json.loads(highlight_usernames)
        else:
            highlight_usernames = []

        if not username in highlight_usernames:
            highlight_usernames.append(username)
            self.controller.config.update_plugin_value(
                self,
                server['server'],
                channel,
                highlight,
                json.dumps(highlight_usernames),
            )
            self._msg(server['server'], user, 'Added highlight trigger.')
        else:
            self._msg(server['server'], user, 'You\'re already subscribed to that trigger.')
        defer.returnValue(STOP)
