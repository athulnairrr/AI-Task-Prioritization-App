import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/src/design/format.dart';

void main() {
  group('Format.greeting', () {
    test('morning', () => expect(Format.greeting(DateTime(2026, 1, 1, 8)), 'Good morning'));
    test('afternoon', () => expect(Format.greeting(DateTime(2026, 1, 1, 14)), 'Good afternoon'));
    test('evening', () => expect(Format.greeting(DateTime(2026, 1, 1, 20)), 'Good evening'));
  });

  test('Format.dayHeading renders weekday + month + day', () {
    // 2026-08-24 is a Monday.
    expect(Format.dayHeading(DateTime(2026, 8, 24)), 'Monday, August 24');
  });

  test('Format.time renders 12-hour clock with AM/PM', () {
    expect(Format.time(DateTime(2026, 1, 1, 9, 5)), '9:05 AM');
    expect(Format.time(DateTime(2026, 1, 1, 13, 30)), '1:30 PM');
    expect(Format.time(DateTime(2026, 1, 1, 0, 0)), '12:00 AM');
    expect(Format.time(DateTime(2026, 1, 1, 12, 0)), '12:00 PM');
  });

  test('Format.timeRange joins two times with an en dash', () {
    expect(
      Format.timeRange(DateTime(2026, 1, 1, 10), DateTime(2026, 1, 1, 12)),
      '10:00 AM – 12:00 PM',
    );
  });

  group('Format.duration', () {
    test('minutes only', () => expect(Format.duration(const Duration(minutes: 45)), '45m'));
    test('hours only', () => expect(Format.duration(const Duration(hours: 2)), '2h'));
    test('hours and minutes', () => expect(Format.duration(const Duration(hours: 1, minutes: 30)), '1h 30m'));
    test('zero', () => expect(Format.duration(Duration.zero), '0m'));
  });

  test('Format.durationMinutes matches Format.duration', () {
    expect(Format.durationMinutes(90), Format.duration(const Duration(minutes: 90)));
  });
}
