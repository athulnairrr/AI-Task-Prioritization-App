import 'package:flutter/material.dart';

import '../app_theme.dart';

enum PriorityTier { high, medium, low }

PriorityTier priorityTierFor(double? score) {
  if (score == null) return PriorityTier.low;
  if (score >= 75) return PriorityTier.high;
  if (score >= 45) return PriorityTier.medium;
  return PriorityTier.low;
}

/// A small colored "Priority 94" pill -- the one recurring visual cue this
/// app uses instead of a chart to communicate urgency at a glance (per the
/// brief: avoid excessive charts).
class PriorityBadge extends StatelessWidget {
  const PriorityBadge({super.key, required this.score});

  final double? score;

  @override
  Widget build(BuildContext context) {
    final tier = priorityTierFor(score);
    final (fg, bg) = switch (tier) {
      PriorityTier.high => (SemanticColors.priorityHigh, SemanticColors.priorityHighBg),
      PriorityTier.medium => (SemanticColors.priorityMedium, SemanticColors.priorityMediumBg),
      PriorityTier.low => (SemanticColors.priorityLow, SemanticColors.priorityLowBg),
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(999)),
      child: Text(
        score == null ? 'Not prioritized' : 'Priority ${score!.toStringAsFixed(0)}',
        style: TextStyle(color: fg, fontSize: 12, fontWeight: FontWeight.w700),
      ),
    );
  }
}
