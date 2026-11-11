import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

class PendingAction {
  final int id;
  final String summary;
  PendingAction(this.id, this.summary);
}

class NovaReply {
  final String heard;
  final String reply;
  final String? audio;
  final List<PendingAction> pending;
  NovaReply(this.heard, this.reply, this.audio, this.pending);

  factory NovaReply.fromJson(Map<String, dynamic> j) => NovaReply(
        j['heard'] ?? '',
        j['reply'] ?? '',
        j['audio'],
        ((j['pending_actions'] ?? []) as List)
            .map((a) => PendingAction(a['id'], a['summary']))
            .toList(),
      );
}

class NovaApi {
  final String baseUrl; // örn. https://mac-adin.tailXXXX.ts.net
  final String token;
  NovaApi(this.baseUrl, this.token);

  Map<String, String> get _h => {'Authorization': 'Bearer $token'};
  Uri _u(String p) => Uri.parse('${baseUrl.replaceAll(RegExp(r'/$'), '')}$p');

  Future<NovaReply> chat(String text) async {
    final r = await http.post(_u('/chat'),
        headers: {..._h, 'Content-Type': 'application/json'},
        body: jsonEncode({'text': text}));
    return NovaReply.fromJson(_check(r));
  }

  Future<NovaReply> voice(String filePath) async {
    final req = http.MultipartRequest('POST', _u('/voice'))
      ..headers.addAll(_h)
      ..files.add(await http.MultipartFile.fromPath('file', filePath));
    final r = await http.Response.fromStream(await req.send());
    return NovaReply.fromJson(_check(r));
  }

  Future<NovaReply> briefing() async =>
      NovaReply.fromJson(_check(await http.post(_u('/briefing'), headers: _h)));

  Future<Map<String, dynamic>> decide(int id, bool approve) async => _check(
      await http.post(_u('/actions/$id/${approve ? 'approve' : 'reject'}'), headers: _h));

  /// Ses dosyası token istediği için önce indirilir, sonra yerelden çalınır.
  Future<String> downloadAudio(String name) async {
    final r = await http.get(_u('/audio/$name'), headers: _h);
    if (r.statusCode != 200) throw Exception('Ses alınamadı (${r.statusCode})');
    final f = File('${(await getTemporaryDirectory()).path}/$name');
    await f.writeAsBytes(r.bodyBytes);
    return f.path;
  }

  Map<String, dynamic> _check(http.Response r) {
    if (r.statusCode != 200) {
      throw Exception('Nova hatası ${r.statusCode}: ${utf8.decode(r.bodyBytes)}');
    }
    return jsonDecode(utf8.decode(r.bodyBytes));
  }
}
