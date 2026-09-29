# 音楽パターン監査（2026-09-29）

`PYTHONPATH=src .venv/bin/python scripts/audit_music_patterns.py` で、Liveや保存済み候補に触れずに再測定できます。Tech House、Liquid Drum & Bass、J-Popを各64小節、seed 3・4・5で生成しました。

| 観察 | 根拠 | 次の改善 |
| --- | --- | --- |
| Lead が3ジャンルとも0（修正前） | 9候補の `parts` に `Lead` がなかった | 明示的なリード指示があれば全ジャンルでLeadを生成するよう修正 |
| Arp の密度が一律（修正前） | 全候補で624ノート、オンセット16枠だった | Liquid DnBは252、J-Popは336、Tech Houseは624ノートに変更（seed 3、64小節） |
| ドラムの差は出ている | Tech HouseのKickは257、Liquid DnBとJ-Popは151ノート | ノート数だけでなく拍位置と試聴で評価する |

この監査はプログラム上のノート配置の比較です。音色、グルーヴの聴感、曲の完成度は判定できません。既存の音楽生成3ファイルに未コミット変更があるため、本監査では上書きしていません。

修正後、候補生成・理論・Live計画の関連テストは86件すべて通過しました。以前の固定ノート列テストは、現在のジャンル和声を反映した値に更新しました。Padの4音が重複で3音になる問題も修正しました。HTTPテストはこの実行環境のローカルソケット権限で実行できませんでした。

次の旋律改善では、Lead の各8小節フレーズにモチーフと終止を保ち、J-Popは拍頭を含む長めの音、Liquid Drum & Bassはオフビート中心の短い音にしました。同じ64小節・seed 3で、LeadはJ-Pop 186ノート、Liquid Drum & Bass 154ノートです。比較用MIDIは `outputs/music-audit/j-pop-melody-v2-seed3.mid` と `outputs/music-audit/liquid-dnb-melody-v2-seed3.mid` に新規保存し、前のMIDIは残しました。これはノート設計の差であり、音楽的な優劣には試聴が必要です。

## 同じ音色でLeadだけを聴く

`scripts/render_midi_lead_preview.py` はKIHACHIが書いたMIDIからLeadトラックを読み、単純な合成音で8小節のWAVを作ります。ドラム、伴奏、エフェクトは含まず、音色の比較にも使えません。旧版・新版それぞれの小節17〜24を以下に保存しました。

| ジャンル | 旧版 | 新版 | 秒数 |
| --- | --- | --- | --- |
| J-Pop | `outputs/music-audit/j-pop-lead-v1-bars17-24.wav` | `outputs/music-audit/j-pop-lead-v2-bars17-24.wav` | 16.0 |
| Liquid DnB | `outputs/music-audit/liquid-dnb-lead-v1-bars17-24.wav` | `outputs/music-audit/liquid-dnb-lead-v2-bars17-24.wav` | 11.0 |

ファイルは新規作成のみで、既存のプレビューやLive Setを書き換えません。実際に聴いた人の判断はまだ記録していません。

## 伴奏との相性を聴く

`scripts/render_midi_arrangement_preview.py` は同じMIDIの小節17〜24から、付属のドラムサンプルと固定の簡易シンセ音でKick・Snare・Hats・Perc・Sub・Bass・Stab・Pad・Arp・Leadをミックスします。音色選択、エフェクト、MIX、マスタリングは本番用ではありません。比較した旧版と新版はLead以外のMIDIノートが同一であることを確認しました。

| ジャンル | 旧版ミックス | 新版ミックス |
| --- | --- | --- |
| J-Pop | `outputs/music-audit/j-pop-mix-v1-bars17-24.wav` | `outputs/music-audit/j-pop-mix-v2-bars17-24.wav` |
| Liquid DnB | `outputs/music-audit/liquid-dnb-mix-v1-bars17-24.wav` | `outputs/music-audit/liquid-dnb-mix-v2-bars17-24.wav` |

この比較で聴けるのはLeadのフレーズが伴奏内でどう収まるかまでです。実際のAbleton音色での評価と、曲全体の展開の評価は別途必要です。

## ジャンル別の曲構成（v3）

64小節でDrop開始位置を明示しない場合、J-Popを `Intro 4 → VerseA 12 → ChorusA 16 → VerseB 8 → Break 4 → ChorusB 16 → Outro 4`、Liquid Drum & Bassを `Intro 8 → VerseA 8 → Build 8 → Drop 16 → Break 8 → Drop 8 → Outro 8` に分けました。Tech Houseの従来構成は変えていません。Drop開始小節が明示されている場合はジャンル既定より指示を優先します。

小節9〜32を同じ簡易音色でミックスした比較は以下です。曲全体やAbleton音色を評価するものではありません。

| ジャンル | 旧構成（v2） | 新構成（v3） |
| --- | --- | --- |
| J-Pop | `outputs/music-audit/j-pop-form-v2-bars9-32.wav` | `outputs/music-audit/j-pop-form-v3-bars9-32.wav` |
| Liquid DnB | `outputs/music-audit/liquid-dnb-form-v2-bars9-32.wav` | `outputs/music-audit/liquid-dnb-form-v3-bars9-32.wav` |

構成変更により各パートの入る小節も変わります。旧版と新版の差はLeadだけには限定されません。新旧MIDIは別名で保存し、元の候補やLive Setは変更していません。
