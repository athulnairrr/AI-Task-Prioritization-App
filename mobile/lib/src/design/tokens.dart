/// Shared spacing/radius constants -- the single place screen code should
/// pull layout numbers from, instead of scattering magic numbers across
/// widgets. Deliberately a small, fixed scale (not a generic design-token
/// package): this app doesn't need more than this.
class Spacing {
  const Spacing._();

  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double xxl = 32;
}

class Corners {
  const Corners._();

  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double pill = 999;
}
