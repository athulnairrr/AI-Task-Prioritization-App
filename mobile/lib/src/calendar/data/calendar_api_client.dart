import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../auth/auth_repository.dart';
import '../../core/env.dart';
import 'calendar_models.dart';

class CalendarApiException implements Exception {
  CalendarApiException(this.statusCode, this.message, {this.code});

  final int statusCode;
  final String message;
  final String? code;

  bool get isReauthRequired => code == 'REAUTH_REQUIRED';

  @override
  String toString() => 'CalendarApiException($statusCode): $message';
}

/// Talks to the FastAPI backend's /calendar routes. Never touches a Google
/// token directly -- the backend never returns one.
///
/// Takes an injectable [http.Client] (defaulting to a real one) so tests
/// can swap in `package:http/testing.dart`'s `MockClient`.
class CalendarApiClient {
  CalendarApiClient({AuthRepository? authRepository, http.Client? client})
      : _authRepository = authRepository ?? AuthRepository(),
        _client = client ?? http.Client();

  final AuthRepository _authRepository;
  final http.Client _client;

  Uri _uri(String path, [Map<String, String>? query]) {
    return Uri.parse('${Env.apiBaseUrl}$path').replace(queryParameters: query);
  }

  Map<String, String> get _headers {
    final token = _authRepository.accessToken;
    if (token == null) {
      throw StateError('No authenticated session -- sign in before calling the calendar API.');
    }
    return {
      'Authorization': 'Bearer $token',
      'Content-Type': 'application/json',
    };
  }

  Never _throwFor(http.Response response) {
    String message = response.body;
    String? code;
    try {
      final decoded = jsonDecode(response.body);
      if (decoded is Map && decoded['detail'] != null) {
        final detail = decoded['detail'];
        if (detail is Map) {
          code = detail['code'] as String?;
          message = (detail['message'] ?? detail).toString();
        } else {
          message = detail.toString();
        }
      }
    } catch (_) {
      // Body wasn't JSON -- fall back to the raw text set above.
    }
    throw CalendarApiException(response.statusCode, message, code: code);
  }

  Future<CalendarConnection> getConnection() async {
    final resp = await _client.get(_uri('/calendar/connection'), headers: _headers);
    if (resp.statusCode != 200) _throwFor(resp);
    return CalendarConnection.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Returns the Google authorization URL for the caller to launch in an
  /// external browser -- this call itself is a normal authenticated
  /// request; the actual browser launch happens separately (see the
  /// Calendar and Settings screens), same shape as the web client.
  Future<String> getConnectUrl() async {
    final resp = await _client.get(_uri('/calendar/connect'), headers: _headers);
    if (resp.statusCode != 200) _throwFor(resp);
    return (jsonDecode(resp.body) as Map<String, dynamic>)['authorization_url'] as String;
  }

  /// Requests the additional calendar.events (write) scope via incremental
  /// authorization -- upgrades the existing connection in place. Used only
  /// by the "Apply Schedule" flow.
  Future<String> getWriteScopeConnectUrl() async {
    final resp = await _client.get(_uri('/calendar/connect', {'scope': 'write'}), headers: _headers);
    if (resp.statusCode != 200) _throwFor(resp);
    return (jsonDecode(resp.body) as Map<String, dynamic>)['authorization_url'] as String;
  }

  Future<void> disconnect() async {
    final resp = await _client.delete(_uri('/calendar/connection'), headers: _headers);
    if (resp.statusCode != 204) _throwFor(resp);
  }

  Future<Availability> getAvailability(DateTime start, DateTime end) async {
    final resp = await _client.get(
      _uri('/calendar/availability', {
        'start': start.toUtc().toIso8601String(),
        'end': end.toUtc().toIso8601String(),
      }),
      headers: _headers,
    );
    if (resp.statusCode != 200) _throwFor(resp);
    return Availability.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Cached, normalized busy blocks from events this app did NOT create
  /// (Phase 7 synced cache) -- cheap to call often, no live Google call.
  Future<List<ExternalCalendarEvent>> getExternalEvents(DateTime start, DateTime end) async {
    final resp = await _client.get(
      _uri('/calendar/external-events', {
        'start': start.toUtc().toIso8601String(),
        'end': end.toUtc().toIso8601String(),
      }),
      headers: _headers,
    );
    if (resp.statusCode != 200) _throwFor(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((e) => ExternalCalendarEvent.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Explicit reconciliation (Phase 7): renews the push-notification watch
  /// channel if needed, then runs an incremental (or full, the first time)
  /// sync inline. Call this when the calendar section mounts/resumes or the
  /// user taps refresh -- not on a timer.
  Future<CalendarSyncResult> syncCalendar() async {
    final resp = await _client.post(_uri('/calendar/sync'), headers: _headers);
    if (resp.statusCode != 200) _throwFor(resp);
    return CalendarSyncResult.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }
}
