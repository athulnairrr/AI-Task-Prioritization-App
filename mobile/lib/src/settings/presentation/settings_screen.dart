import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../auth/auth_repository.dart';
import '../../calendar/data/calendar_api_client.dart';
import '../../calendar/data/calendar_models.dart';
import '../../design/tokens.dart';

/// Account + Google Calendar connection + sync status + read-only
/// working-hours/timezone info + sign out. No subscriptions/billing
/// (out of MVP scope) and no editable working-hours/timezone yet -- the
/// scheduling engine's working hours are a fixed server-side default and
/// the timezone shown here is the connected calendar's own, not a
/// separate user preference (see /docs/architecture.md "Timezone
/// strategy"); this screen surfaces both for transparency rather than
/// pretending they're configurable.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _authRepository = AuthRepository();
  final _calendarApi = CalendarApiClient();

  CalendarConnection? _connection;
  bool _loading = true;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final connection = await _calendarApi.getConnection();
      if (mounted) setState(() => _connection = connection);
    } catch (_) {
      // Non-fatal -- the rest of Settings still renders.
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _connect() async {
    setState(() => _busy = true);
    try {
      final url = await _calendarApi.getConnectUrl();
      await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Could not start connection.')));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _disconnect() async {
    setState(() => _busy = true);
    try {
      await _calendarApi.disconnect();
      await _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Could not disconnect.')));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final user = _authRepository.currentUser;
    final connection = _connection;
    final connected = connection?.status == CalendarConnectionStatus.connected;

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.symmetric(vertical: Spacing.md),
              children: [
                const _SectionHeader('Account'),
                ListTile(
                  leading: const Icon(Icons.person_outline),
                  title: Text(user?.email ?? 'Signed in'),
                  subtitle: const Text('Signed in with Supabase'),
                ),
                const Divider(height: 1),
                const _SectionHeader('Google Calendar'),
                ListTile(
                  leading: Icon(
                    connected ? Icons.check_circle : Icons.cancel_outlined,
                    color: connected ? const Color(0xFF16A34A) : Theme.of(context).colorScheme.outline,
                  ),
                  title: Text(connected ? 'Connected' : 'Not connected'),
                  subtitle: Text(
                    connected
                        ? connection!.googleAccountEmail ?? ''
                        : 'Connect to plan around your real availability.',
                  ),
                  trailing: _busy
                      ? const SizedBox(height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
                      : TextButton(
                          onPressed: connected ? _disconnect : _connect,
                          child: Text(connected ? 'Disconnect' : 'Connect'),
                        ),
                ),
                if (connected) ...[
                  ListTile(
                    leading: Icon(connection!.watchActive ? Icons.sync : Icons.sync_disabled),
                    title: Text(connection.watchActive ? 'Live sync active' : 'Manual sync only'),
                    subtitle: Text('Last synced: ${connection.lastSyncedAt?.toLocal() ?? 'never'}'),
                  ),
                  ListTile(
                    leading: const Icon(Icons.public),
                    title: const Text('Timezone'),
                    subtitle: Text(connection.calendarTimezone ?? 'UTC (default)'),
                  ),
                ],
                const Divider(height: 1),
                const _SectionHeader('Scheduling'),
                const ListTile(
                  leading: Icon(Icons.schedule_outlined),
                  title: Text('Working hours'),
                  subtitle: Text('9:00 AM – 6:00 PM (default, applies to every day)'),
                ),
                const Divider(height: 1),
                const SizedBox(height: Spacing.md),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: Spacing.lg),
                  child: OutlinedButton.icon(
                    onPressed: () => _authRepository.signOut(),
                    icon: const Icon(Icons.logout),
                    label: const Text('Sign out'),
                  ),
                ),
              ],
            ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(Spacing.lg, Spacing.lg, Spacing.lg, Spacing.xs),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.6,
          color: Theme.of(context).colorScheme.primary,
        ),
      ),
    );
  }
}
