<script setup lang="ts">
import { computed } from 'vue'
import { DataAnalysis, Headset, VideoCamera } from '@element-plus/icons-vue'
import type { RecentVideo } from '@/types/api'

const props = defineProps<{
  video: RecentVideo | null
  parsing: boolean
  sourceReady: boolean
}>()

const waveform = [38, 72, 44, 86, 56, 96, 48, 68, 84, 42, 76, 54, 92, 46, 64, 82, 52, 74, 40, 88]
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
    aria-label="视频分层解析预览"
  >
    <div class="scene-grid vertical-grid" aria-hidden="true"><i v-for="index in 10" :key="index" /></div>
    <div class="scene-grid horizontal-grid" aria-hidden="true"><i v-for="index in 6" :key="index" /></div>

    <div class="scene-status">
      <span class="status-node" aria-hidden="true" />
      <span>{{ status.label }}</span>
      <code>{{ status.code }}</code>
    </div>

    <div class="scene-readout">
      <dl><dt>IDENTITY</dt><dd>{{ video?.bvid ?? 'NOT MAPPED' }}</dd></dl>
      <dl><dt>DURATION</dt><dd>{{ video ? formatDuration(video.duration) : '--:--' }}</dd></dl>
      <dl><dt>OWNER</dt><dd>{{ video?.ownerName ?? 'UNKNOWN' }}</dd></dl>
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
  </div>
</template>

<style scoped>
.xray-scene {
  --scene-line: rgba(49, 73, 78, .075);
  --scene-muted: #899694;
  --scene-signal: #0c8d85;
  --scene-hot: #ef684d;
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: #f1f5f4;
  color: var(--text-primary);
}
.scene-grid { position: absolute; inset: 0; display: grid; }
.vertical-grid { grid-template-columns: repeat(10, 1fr); }.horizontal-grid { grid-template-rows: repeat(6, 1fr); }
.vertical-grid i { border-left: 1px solid var(--scene-line); }.horizontal-grid i { border-top: 1px solid var(--scene-line); }
.scene-status { position: absolute; top: 16px; left: 18px; z-index: 7; display: flex; align-items: center; gap: 8px; color: var(--text-secondary); font-size: 10px; font-weight: 700; }
.scene-status code { margin-left: 5px; color: var(--scene-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; }
.status-node { width: 7px; height: 7px; border-radius: 50%; background: var(--scene-muted); box-shadow: 0 0 0 4px rgba(137, 150, 148, .13); }
.has-source .status-node { background: var(--scene-signal); box-shadow: 0 0 0 4px rgba(12, 141, 133, .12); }
.scene-readout { position: absolute; top: 58px; left: 18px; z-index: 3; display: grid; width: 168px; gap: 1px; border: 1px solid var(--line-soft); background: var(--line-soft); }
.scene-readout dl { display: grid; grid-template-columns: 58px minmax(0, 1fr); gap: 7px; min-width: 0; margin: 0; padding: 7px 8px; background: rgba(255, 255, 255, .92); }
.scene-readout dt { color: var(--scene-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 6px; }.scene-readout dd { overflow: hidden; margin: 0; color: var(--text-secondary); font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; text-overflow: ellipsis; white-space: nowrap; }
.source-object { position: absolute; top: 10%; right: 4%; z-index: 5; width: 55%; }
.source-frame { position: relative; z-index: 3; display: block; aspect-ratio: 16 / 9; overflow: hidden; border: 1px solid #cfdad8; background: white; color: inherit; text-decoration: none; box-shadow: 0 18px 38px rgba(36, 61, 64, .13); }
.source-frame img { width: 100%; height: 100%; object-fit: cover; }
.source-frame::after { position: absolute; inset: 10px; border: 1px solid rgba(255, 255, 255, .6); content: ''; pointer-events: none; }
.frame-echo { position: absolute; inset: 0; border: 1px solid rgba(12, 141, 133, .3); }
.echo-one { transform: translate(10px, 10px); }.echo-two { transform: translate(20px, 20px); opacity: .48; }
.scan-beam { position: absolute; z-index: 2; top: 5%; bottom: 5%; left: 18%; width: 1px; background: var(--scene-hot); box-shadow: 0 0 12px rgba(239, 104, 77, .65); animation: scan-source 5.4s ease-in-out infinite; }
.corner { position: absolute; z-index: 4; width: 12px; height: 12px; border-color: var(--scene-signal); }
.corner-a { top: 10px; left: 10px; border-top: 2px solid; border-left: 2px solid; }.corner-b { top: 10px; right: 10px; border-top: 2px solid; border-right: 2px solid; }
.corner-c { bottom: 10px; left: 10px; border-bottom: 2px solid; border-left: 2px solid; }.corner-d { right: 10px; bottom: 10px; border-right: 2px solid; border-bottom: 2px solid; }
.source-badge { position: absolute; top: 15px; left: 17px; z-index: 5; padding: 4px 6px; background: var(--brand); color: white; font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; }
.source-title { position: absolute; right: 0; bottom: 0; left: 0; z-index: 5; overflow: hidden; padding: 10px 14px; background: rgba(255, 255, 255, .92); color: var(--text-primary); font-size: 10px; font-weight: 750; text-overflow: ellipsis; white-space: nowrap; }
.source-empty { display: grid; place-content: center; justify-items: center; gap: 5px; color: var(--scene-muted); }
.source-empty strong { color: var(--text-secondary); font-size: 13px; }.source-empty small { font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; }
.empty-reticle { position: relative; width: 48px; height: 48px; margin-bottom: 3px; border: 1px solid #c9d7d4; border-radius: 50%; }
.empty-reticle::before, .empty-reticle::after, .empty-reticle i { position: absolute; background: var(--scene-signal); content: ''; }
.empty-reticle::before { top: 50%; right: -7px; left: -7px; height: 1px; }.empty-reticle::after { top: -7px; bottom: -7px; left: 50%; width: 1px; }
.empty-reticle i:first-child { inset: 18px; border-radius: 50%; }.empty-reticle i:last-child { inset: 23px; border-radius: 50%; background: var(--scene-hot); }
.track-field { position: absolute; top: 56%; right: 2.5%; left: 4%; z-index: 6; display: grid; gap: 7px; }
.media-track { display: grid; grid-template-columns: 30px 24px minmax(92px, .4fr) minmax(130px, 1fr) minmax(150px, .72fr); align-items: center; min-width: 0; height: 55px; padding: 0 13px; border: 1px solid #c9e2df; border-left: 3px solid var(--scene-signal); border-radius: 5px; background: #e8f4f2; box-shadow: 0 9px 24px rgba(35, 61, 63, .09); animation: extract-track .7s both; }
.audio-track { margin-left: 5%; border-color: #d9e4e2; border-left-color: #7fbdb7; background: rgba(255, 255, 255, .98); animation-delay: .12s; }.data-track { margin-left: 10%; border-color: #efd5ce; border-left-color: var(--scene-hot); background: #fff3ef; animation-delay: .24s; }
.track-number { color: var(--scene-signal); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }.data-track .track-number { color: var(--scene-hot); }
.media-track > .el-icon { color: var(--text-secondary); font-size: 16px; }
.track-name strong, .track-name small { display: block; }.track-name strong { font-size: 9px; }.track-name small { margin-top: 2px; color: var(--scene-muted); font-size: 7px; }
.track-spec { overflow: hidden; color: var(--scene-muted); font-family: "SFMono-Regular", Consolas, monospace; font-size: 6px; text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.frame-samples { display: flex; align-items: end; gap: 4px; height: 22px; }.frame-samples i { flex: 1; min-width: 3px; border-top: 2px solid var(--scene-signal); background: rgba(12, 141, 133, .08); }
.waveform { display: flex; align-items: center; justify-content: center; gap: 2px; height: 26px; overflow: hidden; }.waveform i { width: 2px; min-height: 3px; background: var(--scene-signal); animation: wave-shift 1.7s ease-in-out infinite alternate; }.waveform i:nth-child(3n) { animation-delay: -.6s; }.waveform i:nth-child(4n) { animation-delay: -1.1s; }
.data-blocks { display: flex; align-items: center; gap: 4px; }.data-blocks span { padding: 3px 5px; border: 1px solid rgba(239, 104, 77, .35); color: #c65d47; font-family: "SFMono-Regular", Consolas, monospace; font-size: 6px; }
.is-parsing .status-node { animation: status-pulse 1s ease-in-out infinite; }.is-parsing .scan-beam { animation-duration: 1.25s; }.is-parsing .media-track { border-color: rgba(12, 141, 133, .5); }

@keyframes scan-source { 0%, 100% { left: 15%; opacity: .35; } 50% { left: 86%; opacity: 1; } }
@keyframes extract-track { from { transform: translateX(26px); } to { transform: translateX(0); } }
@keyframes wave-shift { from { transform: scaleY(.5); opacity: .55; } to { transform: scaleY(1); opacity: 1; } }
@keyframes status-pulse { 50% { box-shadow: 0 0 0 9px rgba(12, 141, 133, 0); } }

@media (max-width: 1199px) {
  .source-object { top: 10%; right: 5%; width: 50%; }
  .track-field { top: 55%; }
  .media-track { grid-template-columns: 26px 22px minmax(82px, .4fr) minmax(110px, 1fr); height: 48px; }
  .track-spec { display: none; }
}

@media (max-width: 767px) {
  .scene-status { top: 9px; left: 10px; font-size: 8px; }.scene-status code { display: none; }.status-node { width: 5px; height: 5px; }
  .scene-readout { display: none; }
  .source-object { top: 9%; right: 8px; width: 58%; }
  .echo-one { transform: translate(6px, 6px); }.echo-two { transform: translate(12px, 12px); }
  .source-frame::after { inset: 6px; }.corner { width: 8px; height: 8px; }.corner-a, .corner-b { top: 6px; }.corner-c, .corner-d { bottom: 6px; }.corner-a, .corner-c { left: 6px; }.corner-b, .corner-d { right: 6px; }
  .source-badge { top: 8px; left: 9px; padding: 2px 4px; font-size: 5px; }.source-title { padding: 6px 8px; font-size: 7px; }
  .track-field { top: 56%; right: 7px; left: 5%; gap: 3px; }
  .media-track { grid-template-columns: 17px 16px minmax(48px, .5fr) minmax(64px, 1fr); height: 27px; padding: 0 6px; border-radius: 3px; }
  .audio-track { margin-left: 5%; }.data-track { margin-left: 10%; }
  .track-number { font-size: 5px; }.media-track > .el-icon { font-size: 10px; }.track-name strong { font-size: 6px; }.track-name small { display: none; }
  .frame-samples { gap: 2px; height: 12px; }.frame-samples i { min-width: 2px; }.waveform { gap: 1px; height: 14px; }.waveform i { width: 1px; }
  .data-blocks { gap: 2px; }.data-blocks span { padding: 2px; font-size: 4px; }.data-blocks span:last-child { display: none; }
}
</style>
