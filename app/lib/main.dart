import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';

const cyan = Color(0xFF3FE0FF);

void main() => runApp(const NovaApp());

class NovaApp extends StatelessWidget {
  const NovaApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'NOVA',
        debugShowCheckedModeBanner: false,
        theme: ThemeData.dark().copyWith(
          scaffoldBackgroundColor: const Color(0xFF05080D),
          colorScheme: const ColorScheme.dark(primary: cyan, secondary: cyan),
        ),
        home: const HomePage(),
      );
}

class Msg {
  final bool fromMe;
  final String text;
  final PendingAction? action;
  bool resolved = false;
  Msg(this.fromMe, this.text, {this.action});
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});
  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final _msgs = <Msg>[];
  final _input = TextEditingController();
  final _scroll = ScrollController();
  final _rec = AudioRecorder();
  final _player = AudioPlayer();
  NovaApi? _api;
  bool _busy = false, _recording = false;

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    final p = await SharedPreferences.getInstance();
    final url = p.getString('url'), token = p.getString('token');
    if (url != null && token != null) {
      setState(() => _api = NovaApi(url, token));
    } else {
      WidgetsBinding.instance.addPostFrameCallback((_) => _openSettings());
    }
  }

  Future<void> _openSettings() async {
    final p = await SharedPreferences.getInstance();
    final url = TextEditingController(text: p.getString('url') ?? 'https://');
    final token = TextEditingController(text: p.getString('token') ?? '');
    if (!mounted) return;
    await showDialog(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Bağlantı'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(controller: url, decoration: const InputDecoration(labelText: 'Tailscale adresi')),
          TextField(controller: token, decoration: const InputDecoration(labelText: 'Token'), obscureText: true),
        ]),
        actions: [
          TextButton(
            onPressed: () async {
              await p.setString('url', url.text.trim());
              await p.setString('token', token.text.trim());
              setState(() => _api = NovaApi(url.text.trim(), token.text.trim()));
              if (c.mounted) Navigator.pop(c);
            },
            child: const Text('Kaydet'),
          )
        ],
      ),
    );
  }

  Future<void> _run(Future<NovaReply> Function(NovaApi) f, {String? mine}) async {
    if (_api == null || _busy) return;
    if (mine != null) _add(Msg(true, mine));
    setState(() => _busy = true);
    try {
      final r = await f(_api!);
      if (mine == null && r.heard.isNotEmpty) _add(Msg(true, r.heard));
      _add(Msg(false, r.reply));
      for (final a in r.pending) {
        _add(Msg(false, a.summary, action: a));
      }
      if (r.audio != null) _play(r.audio!);
    } catch (e) {
      _add(Msg(false, 'Bağlantı sorunu efendim: $e'));
    } finally {
      setState(() => _busy = false);
    }
  }

  Future<void> _play(String name) async {
    try {
      await _player.play(DeviceFileSource(await _api!.downloadAudio(name)));
    } catch (_) {}
  }

  void _add(Msg m) {
    setState(() => _msgs.add(m));
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(_scroll.position.maxScrollExtent,
            duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      }
    });
  }

  Future<void> _decide(Msg m, bool approve) async {
    setState(() => m.resolved = true);
    try {
      final r = await _api!.decide(m.action!.id, approve);
      _add(Msg(false, r['reply'] ?? ''));
      if (r['audio'] != null) _play(r['audio']);
    } catch (e) {
      setState(() => m.resolved = false);
      _add(Msg(false, 'Hata: $e'));
    }
  }

  Future<void> _startRec() async {
    if (!await _rec.hasPermission()) return;
    await _player.stop();
    final path = '${(await getTemporaryDirectory()).path}/cmd.m4a';
    await _rec.start(const RecordConfig(encoder: AudioEncoder.aacLc), path: path);
    setState(() => _recording = true);
  }

  Future<void> _stopRec() async {
    final path = await _rec.stop();
    setState(() => _recording = false);
    if (path != null && File(path).existsSync()) {
      _run((api) => api.voice(path));
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          title: const Text('N.O.V.A.', style: TextStyle(letterSpacing: 6, color: cyan)),
          actions: [
            IconButton(icon: const Icon(Icons.wb_sunny_outlined), tooltip: 'Brifing',
                onPressed: () => _run((a) => a.briefing(), mine: 'Brifing')),
            IconButton(icon: const Icon(Icons.settings), onPressed: _openSettings),
          ],
        ),
        body: Column(children: [
          Expanded(
            child: ListView.builder(
              controller: _scroll,
              padding: const EdgeInsets.all(12),
              itemCount: _msgs.length,
              itemBuilder: (_, i) => _bubble(_msgs[i]),
            ),
          ),
          if (_busy) const LinearProgressIndicator(minHeight: 2),
          _inputBar(),
        ]),
      );

  Widget _bubble(Msg m) {
    final box = Container(
      margin: const EdgeInsets.symmetric(vertical: 4),
      padding: const EdgeInsets.all(12),
      constraints: const BoxConstraints(maxWidth: 320),
      decoration: BoxDecoration(
        color: m.fromMe ? cyan.withOpacity(.15) : const Color(0xFF0E1620),
        border: Border.all(color: m.action != null ? Colors.amber : cyan.withOpacity(.35)),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (m.action != null)
          const Text('ONAY GEREKİYOR', style: TextStyle(color: Colors.amber, fontSize: 11, letterSpacing: 2)),
        Text(m.text),
        if (m.action != null && !m.resolved)
          Row(mainAxisAlignment: MainAxisAlignment.end, children: [
            TextButton(onPressed: () => _decide(m, false), child: const Text('Reddet')),
            FilledButton(onPressed: () => _decide(m, true), child: const Text('Onayla')),
          ]),
      ]),
    );
    return Align(alignment: m.fromMe ? Alignment.centerRight : Alignment.centerLeft, child: box);
  }

  Widget _inputBar() => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 6, 8, 8),
          child: Row(children: [
            Expanded(
              child: TextField(
                controller: _input,
                decoration: const InputDecoration(hintText: 'Emredin efendim…', border: InputBorder.none),
                onSubmitted: _send,
              ),
            ),
            GestureDetector(
              onLongPressStart: (_) => _startRec(),
              onLongPressEnd: (_) => _stopRec(),
              child: CircleAvatar(
                radius: 26,
                backgroundColor: _recording ? Colors.redAccent : cyan,
                child: Icon(_recording ? Icons.graphic_eq : Icons.mic, color: Colors.black),
              ),
            ),
            IconButton(icon: const Icon(Icons.send, color: cyan), onPressed: () => _send(_input.text)),
          ]),
        ),
      );

  void _send(String t) {
    t = t.trim();
    if (t.isEmpty) return;
    _input.clear();
    _run((a) => a.chat(t), mine: t);
  }
}
