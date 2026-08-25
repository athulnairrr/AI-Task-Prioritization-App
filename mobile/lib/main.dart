import 'package:flutter/material.dart';

import 'src/auth/presentation/auth_gate.dart';
import 'src/core/env.dart';
import 'src/core/supabase_client.dart';
import 'src/design/app_theme.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Env.load();
  await initSupabase();
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'AI Work Planner',
      theme: AppTheme.light,
      home: const AuthGate(),
    );
  }
}
