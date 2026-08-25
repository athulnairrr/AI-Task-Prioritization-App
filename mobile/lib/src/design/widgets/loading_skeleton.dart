import 'package:flutter/material.dart';

import '../tokens.dart';

/// A subtle pulsing placeholder block -- no shimmer package needed (that
/// would be a new dependency for a one-line animation any `AnimatedOpacity`
/// loop already covers). Use for "we know the shape of this content but
/// haven't fetched it yet" loading states.
class SkeletonBox extends StatefulWidget {
  const SkeletonBox({super.key, this.width, required this.height, this.borderRadius});

  final double? width;
  final double height;
  final BorderRadius? borderRadius;

  @override
  State<SkeletonBox> createState() => _SkeletonBoxState();
}

class _SkeletonBoxState extends State<SkeletonBox> with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 900))
      ..repeat(reverse: true);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final base = Theme.of(context).colorScheme.surfaceContainerHighest;
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        return Container(
          width: widget.width,
          height: widget.height,
          decoration: BoxDecoration(
            color: base.withValues(alpha: 0.35 + _controller.value * 0.25),
            borderRadius: widget.borderRadius ?? BorderRadius.circular(Corners.sm),
          ),
        );
      },
    );
  }
}

/// A skeleton for a single list-tile-shaped card (title line + subtitle
/// line) -- the shape most of this app's lists (tasks, schedule items,
/// calendar entries) share.
class SkeletonListTile extends StatelessWidget {
  const SkeletonListTile({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(Spacing.lg),
      margin: const EdgeInsets.only(bottom: Spacing.sm),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        borderRadius: BorderRadius.circular(Corners.lg),
        border: Border.all(color: Theme.of(context).colorScheme.outlineVariant.withValues(alpha: 0.6)),
      ),
      child: const Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SkeletonBox(width: 160, height: 14),
          SizedBox(height: Spacing.sm),
          SkeletonBox(width: 100, height: 12),
        ],
      ),
    );
  }
}

/// A vertical stack of [SkeletonListTile]s, for a whole list's loading state.
class SkeletonList extends StatelessWidget {
  const SkeletonList({super.key, this.count = 3});

  final int count;

  @override
  Widget build(BuildContext context) {
    return Column(children: List.generate(count, (_) => const SkeletonListTile()));
  }
}
