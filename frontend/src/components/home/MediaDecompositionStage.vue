<script setup lang="ts">
import { computed } from 'vue'
import { DataAnalysis, Headset, VideoCamera } from '@element-plus/icons-vue'
import type { RecentVideo } from '@/types/api'

const props = defineProps<{
  video: RecentVideo | null
  parsing: boolean
  sourceReady: boolean
}>()

const waveform = [38, 72, 44, 86, 56, 96, 48, 68, 84, 42, 76, 54, 92, 46, 64, 82, 52, 74, 40, 88, 58, 70, 50, 80]
const frameSamples = [32, 58, 78, 46, 88, 62, 38, 72]

const status = computed(() => {
  if (props.parsing) return { label: '正在展开媒体结构', code: 'EXTRACTING' }
  if (props.sourceReady) return { label: '视频源已锁定', code: 'SOURCE LOCKED' }
  if (props.video) return { label: '最近解析图谱', code: 'MAP READY' }
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
  <div
    class="xray-scene"
    :class="{ 'is-parsing': parsing, 'has-source': sourceReady || video }"
    data-testid="decomposition-stage"
    aria-label="视频 X-Ray 分层解析场景"
  >
    <div class="scene-grid vertical-grid" aria-hidden="true"><i v-for="index in 12" :key="index" /></div>
    <div class="scene-grid horizontal-grid" aria-hidden="true"><i v-for="index in 8" :key="index" /></div>

    <div class="scene-status">
      <span class="status-node" aria-hidden="true" />
      <span>{{ status.label }}</span>
      <code>{{ status.code }}</code>
    </div>

    <div class="source-object">
      <span class="frame-echo echo-one" aria-hidden="true" />
      <span class="frame-echo echo-two" aria-hidden="true" />
      <RouterLink v-if="video" class="source-frame" :to="`/videos/${video.id}`" :aria-label="`查看 ${video.title}`">
        <img :src="video.coverUrl" :alt="video.title">
        <span class="scan-beam" aria-hidden="true" />
        <span class="corner corner-a" aria-hidden="true" /><span class="corner corner-b" aria-hidden="true" />
        <span class="corner corner-c" aria-hidden="true" /><span class="corner corner-d" aria-hidden="true" />
        <span class="source-badge">00 / SOURCE</span>
        <span class="source-title">{{ video.title }}</span>
      </RouterLink>
      <div v-else class="source-frame source-empty">
        <span class="scan-beam" aria-hidden="true" />
        <span class="empty-reticle" aria-hidden="true"><i /><i /></span>
        <strong>等待视频源</strong>
        <small>URL / BV / AV / SS / EP</small>
      </div>
    </div>

    <div class="extraction-axis" aria-hidden="true"><span /><i /><span /><i /><span /></div>

    <div class="track-field">
      <article class="media-track video-track">
        <div class="track-number">01</div>
        <el-icon><VideoCamera /></el-icon>
        <div class="track-name"><strong>VIDEO</strong><small>画面轨</small></div>
        <div class="frame-samples" aria-hidden="true">
          <i v-for="(height, index) in frameSamples" :key="index" :style="{ height: `${height}%` }" />
        </div>
        <span class="track-spec">RESOLUTION · CODEC · FPS · HDR</span>
      </article>

      <article class="media-track audio-track">
        <div class="track-number">02</div>
        <el-icon><Headset /></el-icon>
        <div class="track-name"><strong>AUDIO</strong><small>声音轨</small></div>
        <div class="waveform" aria-hidden="true">
          <i v-for="(height, index) in waveform" :key="index" :style="{ height: `${height}%` }" />
        </div>
        <span class="track-spec">BITRATE · CHANNEL · SAMPLE · CODEC</span>
      </article>

      <article class="media-track data-track">
        <div class="track-number">03</div>
        <el-icon><DataAnalysis /></el-icon>
        <div class="track-name"><strong>INTELLIGENCE</strong><small>内容层</small></div>
        <div class="data-blocks" aria-hidden="true"><span>META</span><span>ASR</span><span>OCR</span><span>SCENE</span></div>
        <span class="track-spec">SUBTITLE · SEMANTIC · TIMELINE</span>
      </article>
    </div>

    <div class="scene-readout">
      <dl><dt>IDENTITY</dt><dd>{{ video?.bvid ?? 'NOT MAPPED' }}</dd></dl>
      <dl><dt>DURATION</dt><dd>{{ video ? formatDuration(video.duration) : '--:--' }}</dd></dl>
      <dl><dt>OWNER</dt><dd>{{ video?.ownerName ?? 'UNKNOWN' }}</dd></dl>
    </div>
  </div>
</template>

<style scoped>
.xray-scene {
  --scene-line: rgba(197, 226, 222, .1);
  --scene-muted: #78908e;
  --scene-signal: #39dfc9;
  --scene-hot: #ff765a;
  position: absolute;
  inset: 0;
  overflow: hidden;
  color: #eef8f6;
  pointer-events: none;
}
.scene-grid { position: absolute; inset: 0; display: grid; opacity: .75; }
.vertical-grid { grid-template-columns: repeat(12, 1fr); }
.horizontal-grid { grid-template-rows: repeat(8, 1fr); }
.vertical-grid i { border-left: 1px solid var(--scene-line); }
.horizontal-grid i { border-top: 1px solid var(--scene-line); }
.scene-status { position: absolute; top: 6.5%; right: 5%; z-index: 6; display: flex; align-items: center; gap: 9px; color: #bfd0cd; font-size: 10px; font-weight: 700; }
.scene-status code { margin-left: 7px; color: var(--scene-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }
.status-node { width: 7px; height: 7px; border-radius: 50%; background: var(--scene-muted); box-shadow: 0 0 0 5px rgba(120, 144, 142, .1); }
.has-source .status-node { background: var(--scene-signal); box-shadow: 0 0 0 5px rgba(57, 223, 201, .12); }
.source-object { position: absolute; top: 10%; right: 6%; z-index: 4; width: min(46%, 640px); }
.source-frame { position: relative; z-index: 3; display: block; aspect-ratio: 16 / 9; overflow: hidden; border: 1px solid rgba(221, 241, 238, .34); background: #070d0f; color: inherit; text-decoration: none; pointer-events: auto; }
.source-frame img { width: 100%; height: 100%; object-fit: cover; }
.source-frame::after { position: absolute; inset: 12px; border: 1px solid rgba(255, 255, 255, .24); content: ''; pointer-events: none; }
.frame-echo { position: absolute; inset: 0; border: 1px solid rgba(57, 223, 201, .26); }
.echo-one { transform: translate(13px, 13px); }.echo-two { transform: translate(26px, 26px); opacity: .45; }
.scan-beam { position: absolute; z-index: 2; top: 5%; bottom: 5%; left: 18%; width: 1px; background: var(--scene-hot); box-shadow: 0 0 18px rgba(255, 118, 90, .9); animation: scan-source 5.4s ease-in-out infinite; }
.corner { position: absolute; z-index: 4; width: 14px; height: 14px; border-color: var(--scene-signal); }
.corner-a { top: 12px; left: 12px; border-top: 2px solid; border-left: 2px solid; }.corner-b { top: 12px; right: 12px; border-top: 2px solid; border-right: 2px solid; }
.corner-c { bottom: 12px; left: 12px; border-bottom: 2px solid; border-left: 2px solid; }.corner-d { right: 12px; bottom: 12px; border-right: 2px solid; border-bottom: 2px solid; }
.source-badge { position: absolute; top: 18px; left: 20px; z-index: 5; padding: 5px 7px; background: #0a1113; color: var(--scene-hot); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }
.source-title { position: absolute; right: 1px; bottom: 1px; left: 1px; z-index: 5; overflow: hidden; padding: 13px 18px; background: rgba(7, 13, 15, .88); color: white; font-size: 12px; font-weight: 750; text-overflow: ellipsis; white-space: nowrap; }
.source-empty { display: grid; place-content: center; justify-items: center; gap: 7px; color: var(--scene-muted); }
.source-empty strong { color: #dce9e7; font-size: 15px; }.source-empty small { font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }
.empty-reticle { position: relative; width: 58px; height: 58px; margin-bottom: 5px; border: 1px solid var(--scene-line); border-radius: 50%; }
.empty-reticle::before, .empty-reticle::after, .empty-reticle i { position: absolute; background: var(--scene-signal); content: ''; }
.empty-reticle::before { top: 50%; right: -9px; left: -9px; height: 1px; }.empty-reticle::after { top: -9px; bottom: -9px; left: 50%; width: 1px; }
.empty-reticle i:first-child { inset: 21px; border-radius: 50%; }.empty-reticle i:last-child { inset: 27px; border-radius: 50%; background: var(--scene-hot); }
.track-field { position: absolute; top: 45%; right: 4%; left: 18%; z-index: 3; display: grid; gap: 9px; }
.media-track { display: grid; grid-template-columns: 34px 28px minmax(112px, .45fr) minmax(150px, 1fr) minmax(170px, .75fr); align-items: center; min-width: 0; height: 62px; padding: 0 17px; border-top: 1px solid rgba(57, 223, 201, .35); border-bottom: 1px solid var(--scene-line); background: rgba(10, 19, 21, .93); box-shadow: 0 14px 28px rgba(0, 0, 0, .12); animation: extract-track .75s both; }
.audio-track { margin-left: 6%; animation-delay: .12s; }.data-track { margin-left: 12%; border-top-color: rgba(255, 118, 90, .55); animation-delay: .24s; }
.track-number { color: var(--scene-signal); font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; }.data-track .track-number { color: var(--scene-hot); }
.media-track > .el-icon { color: #dce9e7; font-size: 18px; }
.track-name strong, .track-name small { display: block; }.track-name strong { font-size: 10px; }.track-name small { margin-top: 3px; color: var(--scene-muted); font-size: 8px; }
.track-spec { overflow: hidden; color: var(--scene-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.frame-samples { display: flex; align-items: end; gap: 5px; height: 25px; }
.frame-samples i { flex: 1; min-width: 4px; border-top: 2px solid var(--scene-signal); background: rgba(57, 223, 201, .08); }
.waveform { display: flex; align-items: center; justify-content: center; gap: 2px; height: 30px; overflow: hidden; }
.waveform i { width: 2px; min-height: 3px; background: var(--scene-signal); animation: wave-shift 1.7s ease-in-out infinite alternate; }
.waveform i:nth-child(3n) { animation-delay: -.6s; }.waveform i:nth-child(4n) { animation-delay: -1.1s; }
.data-blocks { display: flex; align-items: center; gap: 5px; }
.data-blocks span { padding: 4px 6px; border: 1px solid rgba(255, 118, 90, .38); color: #ff9c88; font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; }
.extraction-axis { position: absolute; top: 42%; right: 3.2%; z-index: 5; display: grid; grid-template-rows: 1fr auto 1fr auto 1fr; justify-items: center; height: 210px; }
.extraction-axis span { width: 1px; height: 100%; background: var(--scene-line); }.extraction-axis i { width: 7px; height: 7px; border: 1px solid var(--scene-signal); border-radius: 50%; background: #091113; }
.scene-readout { position: absolute; bottom: 190px; left: 4.5%; z-index: 4; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); width: 390px; gap: 1px; background: var(--scene-line); }
.scene-readout dl { min-width: 0; margin: 0; padding: 8px 10px; background: rgba(9, 17, 19, .9); }
.scene-readout dt { color: var(--scene-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; }.scene-readout dd { overflow: hidden; margin: 0; color: #cde0dd; font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.is-parsing .status-node { animation: status-pulse 1s ease-in-out infinite; }
.is-parsing .scan-beam { animation-duration: 1.25s; }
.is-parsing .media-track { border-top-color: var(--scene-signal); }

@keyframes scan-source { 0%, 100% { left: 15%; opacity: .35; } 50% { left: 86%; opacity: 1; } }
@keyframes extract-track { from { opacity: 0; transform: translateX(42px); } to { opacity: 1; transform: translateX(0); } }
@keyframes wave-shift { from { transform: scaleY(.48); opacity: .5; } to { transform: scaleY(1); opacity: 1; } }
@keyframes status-pulse { 50% { box-shadow: 0 0 0 11px rgba(57, 223, 201, 0); } }

@media (max-width: 1199px) {
  .source-object { top: 24%; right: 8%; width: 56%; }
  .track-field { top: 53%; left: 9%; }
  .media-track { grid-template-columns: 28px 24px minmax(100px, .4fr) minmax(120px, 1fr); height: 55px; }
  .track-spec { display: none; }
  .scene-readout { display: none; }
  .extraction-axis { top: 51%; }
}

@media (max-width: 767px) {
  .scene-status { top: 22%; right: 16px; font-size: 8px; }.scene-status code { display: none; }.status-node { width: 5px; height: 5px; }
  .scene-readout, .extraction-axis { display: none; }
  .source-object { top: 25%; right: auto; left: 16px; width: 70%; }
  .echo-one { transform: translate(7px, 7px); }.echo-two { transform: translate(14px, 14px); }
  .source-frame::after { inset: 7px; }.corner { width: 9px; height: 9px; }.corner-a, .corner-b { top: 7px; }.corner-c, .corner-d { bottom: 7px; }.corner-a, .corner-c { left: 7px; }.corner-b, .corner-d { right: 7px; }
  .source-badge { top: 10px; left: 11px; padding: 3px 4px; font-size: 6px; }.source-title { padding: 7px 9px; font-size: 8px; }
  .track-field { top: 48%; right: 12px; left: 28%; gap: 5px; }
  .media-track { grid-template-columns: 19px 18px minmax(54px, .55fr) minmax(70px, 1fr); height: 35px; padding: 0 7px; }
  .audio-track { margin-left: 5%; }.data-track { margin-left: 10%; }
  .track-number { font-size: 6px; }.media-track > .el-icon { font-size: 12px; }.track-name strong { font-size: 7px; }.track-name small { display: none; }
  .frame-samples { gap: 2px; height: 14px; }.frame-samples i { min-width: 2px; }.waveform { gap: 1px; height: 16px; }.waveform i { width: 1px; }
  .data-blocks { gap: 2px; }.data-blocks span { padding: 2px 3px; font-size: 5px; }.data-blocks span:last-child { display: none; }
}
</style>
