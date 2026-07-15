from __future__ import annotations

import os
import apprise


class Notifications:
    def __init__(self, extra_config_notifications: str | None) -> None:
        asset = apprise.AppriseAsset()
        asset.app_id = 'craigscraper'
        asset.app_desc = 'craigscraper'
        asset.app_url = 'https://github.com/porelli/craigscraper'

        self.apobj = apprise.Apprise(asset=asset)

        config = apprise.AppriseConfig()

        env_config = os.environ.get('NOTIFICATION_FILE')
        # if a configuration file is provided via ENV...
        if env_config:
            # ...check if it exists...
            if os.path.isfile(env_config):
                # ..and add to apprise
                config.add(env_config)
            else:
                # ...and return an error if file does not exist
                raise FileNotFoundError('The specified notifications file in ENV does not exist')
        else:
            # if a configuration file is provided via CLI...
            if extra_config_notifications:
                # ...check if it exists...
                if os.path.isfile(extra_config_notifications):
                    # ..and add to apprise
                    config.add(extra_config_notifications)
                else:
                    # ...and return an error if file does not exist
                    raise FileNotFoundError('The specified notifications file via CLI does not exist')
            else:
                # ...if it wasn't, add the default configuration
                config.add(os.path.join(os.path.dirname(__file__), 'resources/notifications.yaml'))

        self.apobj.add(config)

        self._log_active_transports()

    def _log_active_transports(self):
        # Surface which notification transports are actually active. This makes a dead or
        # placeholder config visible instead of silently sending nowhere — the default
        # bundled config only has desktop transports (dbus/macosx/windows) which do nothing
        # inside a headless container, so notifications appear "configured" but never arrive.
        # Purely diagnostic: never let it break Notifications.__init__ (which would kill the crawler).
        try:
            schemes = []
            for server in self.apobj:
                scheme = getattr(server, 'protocol', None) or server.__class__.__name__
                if isinstance(scheme, (list, tuple, set)):
                    scheme = next(iter(scheme), None)
                schemes.append(str(scheme))

            desktop_only_schemes = {'dbus', 'qt', 'glib', 'macosx', 'windows', 'gnome'}

            if not schemes:
                print('\033[91mNOTIFICATIONS: no transports configured — nothing will be sent.\033[0m')
            elif all(s in desktop_only_schemes for s in schemes):
                print('\033[93mNOTIFICATIONS: only desktop transports active (%s). These do NOT work '
                      'in a headless/Docker environment — set NOTIFICATION_FILE (or mount '
                      '/persist/notifications.yaml) with a real apprise URL to actually receive '
                      'alerts.\033[0m' % ', '.join(schemes))
            else:
                print('\033[92mNOTIFICATIONS: active transports: %s\033[0m' % ', '.join(schemes))
        except Exception as e:
            print('NOTIFICATIONS: could not inspect active transports (%s)' % e)
