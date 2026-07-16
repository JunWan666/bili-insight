<script setup lang="ts">
import { computed } from 'vue'
import { DataAnalysis, Headset, VideoCamera } from '@element-plus/icons-vue'
import type { RecentVideo } from '@/types/api'

const props = defineProps<{
  video: RecentVideo | null
  parsing: boolean
  sourceReady: boolean
  compact?: boolean
}>()

const waveform = [42, 68, 34, 78, 52, 88, 46, 62, 94, 58, 76, 38, 84, 54, 72, 44, 66, 86, 48, 70]

const stageState = computed(() => {
  if (props.parsing) return { label: '正在拆解媒体层', code: 'ANALYZING' }
  if (props.sourceReady) return { label: '视频源已就绪', code: 'SOURCE READY' }
  if (props.video) return { label: '上次解析结果', code: 'STRUCTURE READY' }
  return { label: '等待视频源', code: 'STANDBY' }
})

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const remain = total % 60
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remain).padStart(2, '0')}`
    : `${minutes}:${String(remain).padStart(2, '0')}`
}
</script>

<template>
  <section
    class="decomposition-stage"
    :class="{ 'is-parsing': parsing, 'has-source': sourceReady || video, 'is-compact': compact }"
    data-testid="decomposition-stage"
    aria-label="视频分层解析预览"
  >
    <header class="stage-header">
      <div class="stage-status">
        <span class="status-pulse" aria-hidden="true" />
        <span>{{ stageState.label }}</span>
      </div>
      <code>{{ stageState.code }}</code>
    </header>

    <div class="stage-canvas">
      <div class="source-column">
        <div class="source-frame">
          <img v-if="video?.coverUrl" :src="video.coverUrl" :alt="video.title">
          <div v-else class="source-placeholder" aria-hidden="true">
            <span>INPUT</span>
            <strong>VIDEO<br>STREAM</strong>
          </div>
          <span class="scan-line" aria-hidden="true" />
          <div class="source-index"><span>00</span><small>SOURCE</small></div>
        </div>

        <div class="source-copy">
          <span>{{ video ? 'RECENT SOURCE' : 'SOURCE CHANNEL' }}</span>
          <RouterLink v-if="video" :to="`/videos/${video.id}`">{{ video.title }}</RouterLink>
          <strong v-else>粘贴链接后建立媒体映射</strong>
          <small>{{ video ? `${video.ownerName} · ${formatDuration(video.duration)}` : '支持投稿、番剧、BV / AV / ss / ep' }}</small>
        </div>
      </div>

      <div class="signal-bridge" aria-hidden="true">
        <span /><i /><span /><i /><span />
      </div>

      <div class="layer-column">
        <article class="media-layer video-layer">
          <div class="layer-index">01</div>
          <el-icon><VideoCamera /></el-icon>
          <div class="layer-copy"><strong>视频轨</strong><small>分辨率 · 编码 · 帧率 · HDR</small></div>
          <div class="video-signal" aria-hidden="true"><span /><span /><span /><span /></div>
          <code>VIDEO</code>
        </article>

        <article class="media-layer audio-layer">
          <div class="layer-index">02</div>
          <el-icon><Headset /></el-icon>
          <div class="layer-copy"><strong>音频轨</strong><small>码率 · 声道 · 采样率 · 编码</small></div>
          <div class="waveform" aria-hidden="true">
            <i v-for="(height, index) in waveform" :key="index" :style="{ height: `${height}%` }" />
          </div>
          <code>AUDIO</code>
        </article>

        <article class="media-layer metadata-layer">
          <div class="layer-index">03</div>
          <el-icon><DataAnalysis /></el-icon>
          <div class="layer-copy"><strong>内容数据</strong><small>元数据 · 字幕 · 场景 · 语义</small></div>
          <div class="metadata-signal" aria-hidden="true"><span>TXT</span><span>OCR</span><span>ASR</span></div>
          <code>DATA</code>
        </article>

        <div class="metadata-readout">
          <dl><dt>IDENTITY</dt><dd>{{ video?.bvid ?? 'NOT MAPPED' }}</dd></dl>
          <dl><dt>CONTAINER</dt><dd>ADAPTIVE STREAMS</dd></dl>
          <dl><dt>PIPELINE</dt><dd>{{ parsing ? 'EXTRACTING' : 'TRACEABLE' }}</dd></dl>
        </div>
      </div>
    </div>

    <footer class="stage-footer">
      <span>01 PARSE</span><i /><span>02 PREVIEW</span><i /><span>03 ANALYZE</span><i /><span>04 EXPORT</span>
    </footer>
  </section>
</template>

<style scoped>
.decomposition-stage {
  --stage-ink: #101820;
  --stage-panel: #18232b;
  --stage-line: rgba(224, 238, 236, .16);
  --stage-muted: #8da19f;
  --stage-signal: #38d6c3;
  --stage-hot: #ff7358;
  position: relative;
  min-width: 0;
  min-height: 540px;
  overflow: hidden;
  border: 1px solid #26343d;
  border-radius: 8px;
  background: var(--stage-ink);
  color: #eef7f5;
  box-shadow: 0 24px 64px rgba(16, 24, 32, .2);
}
.decomposition-stage::before {
  position: absolute;
  inset: 0 auto 0 32px;
  width: 1px;
  background: rgba(255, 255, 255, .03);
  box-shadow: 32px 0 rgba(255, 255, 255, .03), 64px 0 rgba(255, 255, 255, .03), 96px 0 rgba(255, 255, 255, .03), 128px 0 rgba(255, 255, 255, .03), 160px 0 rgba(255, 255, 255, .03), 192px 0 rgba(255, 255, 255, .03), 224px 0 rgba(255, 255, 255, .03), 256px 0 rgba(255, 255, 255, .03), 288px 0 rgba(255, 255, 255, .03), 320px 0 rgba(255, 255, 255, .03), 352px 0 rgba(255, 255, 255, .03), 384px 0 rgba(255, 255, 255, .03), 416px 0 rgba(255, 255, 255, .03), 448px 0 rgba(255, 255, 255, .03), 480px 0 rgba(255, 255, 255, .03), 512px 0 rgba(255, 255, 255, .03), 544px 0 rgba(255, 255, 255, .03), 576px 0 rgba(255, 255, 255, .03), 608px 0 rgba(255, 255, 255, .03);
  content: '';
  pointer-events: none;
}
.stage-header, .stage-footer { position: relative; z-index: 2; display: flex; align-items: center; }
.stage-header { justify-content: space-between; height: 52px; padding: 0 20px; border-bottom: 1px solid var(--stage-line); }
.stage-status { display: flex; align-items: center; gap: 9px; color: #c8d6d4; font-size: 11px; font-weight: 700; }
.status-pulse { width: 7px; height: 7px; border-radius: 50%; background: var(--stage-muted); box-shadow: 0 0 0 4px rgba(141, 161, 159, .1); }
.has-source .status-pulse { background: var(--stage-signal); box-shadow: 0 0 0 4px rgba(56, 214, 195, .12); }
.stage-header code, .media-layer code { color: var(--stage-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; }
.stage-canvas { position: relative; z-index: 1; display: grid; grid-template-columns: minmax(0, .82fr) 42px minmax(0, 1.18fr); min-height: 436px; padding: 28px 24px 22px; }
.source-column { align-self: center; min-width: 0; }
.source-frame { position: relative; aspect-ratio: 16 / 10; overflow: hidden; border: 1px solid rgba(224, 238, 236, .24); background: #0b1116; }
.source-frame::after { position: absolute; inset: 10px; border: 1px solid rgba(255, 255, 255, .2); content: ''; pointer-events: none; }
.source-frame img { width: 100%; height: 100%; object-fit: cover; filter: saturate(.78) contrast(1.06); opacity: .88; }
.source-placeholder { display: grid; align-content: space-between; height: 100%; padding: 22px; color: var(--stage-muted); }
.source-placeholder span { font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; }
.source-placeholder strong { color: #dce9e7; font-size: 29px; line-height: .92; }
.scan-line { position: absolute; z-index: 2; top: 12%; bottom: 12%; left: 18%; width: 1px; background: var(--stage-hot); box-shadow: 0 0 14px rgba(255, 115, 88, .75); animation: scan-source 4.8s ease-in-out infinite; }
.source-index { position: absolute; z-index: 3; right: 16px; bottom: 14px; display: flex; align-items: baseline; gap: 7px; padding: 5px 7px; background: rgba(10, 16, 21, .82); }
.source-index span { color: var(--stage-hot); font-family: "SFMono-Regular", Consolas, monospace; font-size: 13px; }
.source-index small { color: #b9c8c6; font-size: 8px; }
.source-copy { display: grid; gap: 5px; min-width: 0; padding-top: 14px; }
.source-copy > span { color: var(--stage-hot); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }
.source-copy a, .source-copy strong { overflow: hidden; color: #f2f7f6; font-size: 14px; font-weight: 750; line-height: 1.4; text-decoration: none; text-overflow: ellipsis; white-space: nowrap; }
.source-copy a:hover { color: var(--stage-signal); }
.source-copy small { overflow: hidden; color: var(--stage-muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.signal-bridge { display: grid; grid-template-rows: 1fr auto 1fr auto 1fr; justify-items: center; padding-block: 34px 50px; }
.signal-bridge span { width: 1px; height: 100%; background: var(--stage-line); }
.signal-bridge i { width: 7px; height: 7px; border: 1px solid var(--stage-signal); border-radius: 50%; background: var(--stage-ink); box-shadow: 0 0 0 4px rgba(56, 214, 195, .07); }
.layer-column { display: grid; align-content: center; gap: 9px; min-width: 0; }
.media-layer { position: relative; display: grid; grid-template-columns: 24px 24px minmax(104px, 1fr) minmax(52px, .85fr) auto; align-items: center; gap: 8px; min-width: 0; min-height: 76px; padding: 10px 12px; border: 1px solid var(--stage-line); border-left: 2px solid var(--stage-signal); background: rgba(24, 35, 43, .88); animation: layer-arrive .7s both; }
.audio-layer { animation-delay: .12s; }
.metadata-layer { border-left-color: var(--stage-hot); animation-delay: .24s; }
.layer-index { color: var(--stage-signal); font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; }
.metadata-layer .layer-index { color: var(--stage-hot); }
.media-layer > .el-icon { color: #dce9e7; font-size: 18px; }
.layer-copy { min-width: 0; }
.layer-copy strong, .layer-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.layer-copy strong { color: #edf6f4; font-size: 12px; }
.layer-copy small { margin-top: 4px; color: var(--stage-muted); font-size: 8px; }
.video-signal { display: grid; grid-template-columns: repeat(4, 1fr); align-items: end; gap: 3px; height: 24px; }
.video-signal span { border-top: 2px solid var(--stage-signal); background: rgba(56, 214, 195, .08); }
.video-signal span:nth-child(1) { height: 35%; }.video-signal span:nth-child(2) { height: 70%; }.video-signal span:nth-child(3) { height: 48%; }.video-signal span:nth-child(4) { height: 88%; }
.waveform { display: flex; align-items: center; gap: 2px; height: 28px; overflow: hidden; }
.waveform i { width: 2px; min-height: 3px; background: var(--stage-signal); transform-origin: center; animation: wave-shift 1.8s ease-in-out infinite alternate; }
.waveform i:nth-child(3n) { animation-delay: -.7s; }.waveform i:nth-child(4n) { animation-delay: -1.1s; }
.metadata-signal { display: flex; flex-wrap: wrap; gap: 3px; }
.metadata-signal span { padding: 3px 4px; border: 1px solid rgba(255, 115, 88, .32); color: #ff9a84; font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; }
.metadata-readout { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; margin-top: 3px; background: var(--stage-line); }
.metadata-readout dl { min-width: 0; margin: 0; padding: 9px 8px; background: rgba(16, 24, 32, .96); }
.metadata-readout dt { margin-bottom: 5px; color: var(--stage-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; }
.metadata-readout dd { overflow: hidden; margin: 0; color: #dfe9e7; font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.stage-footer { justify-content: center; gap: 8px; height: 52px; padding: 0 18px; border-top: 1px solid var(--stage-line); color: var(--stage-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; }
.stage-footer i { width: 18px; height: 1px; background: var(--stage-line); }
.is-parsing .status-pulse { animation: status-pulse 1s ease-in-out infinite; }
.is-parsing .media-layer { border-color: rgba(56, 214, 195, .55); }
.is-parsing .scan-line { animation-duration: 1.3s; }
.is-compact { min-height: 310px; height: 310px; }
.is-compact .stage-header { height: 42px; }
.is-compact .stage-canvas { grid-template-columns: minmax(0, .75fr) 28px minmax(0, 1.25fr); min-height: 232px; height: 232px; padding: 14px 16px 10px; }
.is-compact .signal-bridge { padding-block: 20px 32px; }
.is-compact .layer-column { gap: 5px; }
.is-compact .media-layer { min-height: 52px; padding: 6px 9px; }
.is-compact .video-signal, .is-compact .waveform { height: 20px; }
.is-compact .metadata-readout dl { padding: 6px; }
.is-compact .stage-footer { height: 36px; }

@keyframes scan-source { 0%, 100% { left: 15%; opacity: .35; } 50% { left: 84%; opacity: 1; } }
@keyframes layer-arrive { from { opacity: 0; transform: translateX(16px); } to { opacity: 1; transform: translateX(0); } }
@keyframes wave-shift { from { transform: scaleY(.55); opacity: .55; } to { transform: scaleY(1); opacity: 1; } }
@keyframes status-pulse { 50% { box-shadow: 0 0 0 8px rgba(56, 214, 195, 0); } }

@media (max-width: 1199px) {
  .decomposition-stage { min-height: 500px; }
  .stage-canvas { min-height: 396px; padding-inline: 18px; }
  .media-layer { grid-template-columns: 20px 22px minmax(92px, 1fr) minmax(40px, .7fr); }
  .media-layer code { display: none; }
}

@media (max-width: 767px) {
  .decomposition-stage { min-height: 0; height: 112px; box-shadow: 0 12px 30px rgba(16, 24, 32, .14); }
  .decomposition-stage::before { left: 24px; box-shadow: 24px 0 rgba(255, 255, 255, .03), 48px 0 rgba(255, 255, 255, .03), 72px 0 rgba(255, 255, 255, .03), 96px 0 rgba(255, 255, 255, .03), 120px 0 rgba(255, 255, 255, .03), 144px 0 rgba(255, 255, 255, .03), 168px 0 rgba(255, 255, 255, .03), 192px 0 rgba(255, 255, 255, .03), 216px 0 rgba(255, 255, 255, .03), 240px 0 rgba(255, 255, 255, .03), 264px 0 rgba(255, 255, 255, .03), 288px 0 rgba(255, 255, 255, .03), 312px 0 rgba(255, 255, 255, .03), 336px 0 rgba(255, 255, 255, .03), 360px 0 rgba(255, 255, 255, .03); }
  .stage-header { height: 30px; padding-inline: 11px; }
  .stage-status { gap: 7px; font-size: 9px; }.status-pulse { width: 5px; height: 5px; }
  .stage-header code { font-size: 7px; }
  .stage-canvas { grid-template-columns: 86px 18px 1fr; min-height: 0; height: 81px; padding: 8px 10px; }
  .source-column { align-self: stretch; }
  .source-frame { height: 64px; aspect-ratio: auto; }
  .source-frame::after { inset: 5px; }
  .source-placeholder { padding: 9px; }.source-placeholder span { font-size: 6px; }.source-placeholder strong { font-size: 13px; }
  .source-index { right: 5px; bottom: 4px; padding: 2px 3px; }.source-index span { font-size: 8px; }.source-index small { display: none; }
  .source-copy, .metadata-readout, .stage-footer, .layer-copy small, .media-layer code, .video-signal, .waveform, .metadata-signal { display: none; }
  .signal-bridge { padding-block: 4px; }
  .signal-bridge i { width: 4px; height: 4px; }
  .layer-column { gap: 3px; }
  .media-layer { grid-template-columns: 16px 18px 1fr; gap: 5px; min-height: 0; height: 19px; padding: 2px 6px; border-left-width: 2px; }
  .layer-index { font-size: 6px; }.media-layer > .el-icon { font-size: 12px; }.layer-copy strong { font-size: 8px; }
}
</style>
