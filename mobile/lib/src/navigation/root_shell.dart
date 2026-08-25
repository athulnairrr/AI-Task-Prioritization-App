import 'package:flutter/material.dart';

import '../calendar/presentation/calendar_screen.dart';
import '../settings/presentation/settings_screen.dart';
import '../scheduling/presentation/plan_screen.dart';
import '../tasks/presentation/prioritized_tasks_screen.dart';
import '../today/presentation/today_screen.dart';

/// The signed-in app's root: five tabs sharing one bottom navigation bar.
/// Each tab's screen is kept alive in an [IndexedStack] rather than
/// rebuilt on every switch -- avoids an unnecessary full-screen refresh
/// each time the user taps between tabs (each screen still refreshes
/// itself on relevant realtime/lifecycle events independently).
class RootShell extends StatefulWidget {
  const RootShell({super.key});

  @override
  State<RootShell> createState() => _RootShellState();
}

class _RootShellState extends State<RootShell> {
  int _index = 0;

  static const _screens = [
    TodayScreen(),
    PrioritizedTasksScreen(),
    PlanScreen(),
    CalendarScreen(),
    SettingsScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(index: _index, children: _screens),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.wb_sunny_outlined), selectedIcon: Icon(Icons.wb_sunny), label: 'Today'),
          NavigationDestination(icon: Icon(Icons.task_alt_outlined), selectedIcon: Icon(Icons.task_alt), label: 'Tasks'),
          NavigationDestination(icon: Icon(Icons.auto_awesome_outlined), selectedIcon: Icon(Icons.auto_awesome), label: 'Plan'),
          NavigationDestination(icon: Icon(Icons.calendar_month_outlined), selectedIcon: Icon(Icons.calendar_month), label: 'Calendar'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }
}
