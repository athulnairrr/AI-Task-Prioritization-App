import 'package:flutter/material.dart';

/// A small uppercase, letter-spaced section header ("HIGH PRIORITY",
/// "YOUR PLAN") -- the recurring section-title style used across Today/
/// Plan/Calendar instead of a plain titleMedium everywhere.
class SectionLabel extends StatelessWidget {
  const SectionLabel(this.text, {super.key, this.trailing});

  final String text;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Row(
      children: [
        Expanded(
          child: Text(
            text.toUpperCase(),
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.8,
              color: scheme.onSurfaceVariant,
            ),
          ),
        ),
        ?trailing,
      ],
    );
  }
}
