export interface VoiceReflectionContext {
  activityTitle: string;
  moduleLabel: string;
  weekLabel: string;
  minimumWords?: number;
  onePointPerLine?: boolean;
}

interface VoiceReflectionResponse {
  text: string;
  language: string;
}

export async function proofreadLearningReflection(
  text: string,
  context: VoiceReflectionContext,
): Promise<VoiceReflectionResponse> {
  const response = await fetch('/learner_api/reflection/proofread/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, ...context }),
  });
  const data = await response.json().catch(() => null) as (VoiceReflectionResponse & { error?: string }) | null;
  if (!response.ok) {
    throw new Error(data?.error || 'The reflection could not be proofread. Please try again.');
  }
  if (!data?.text) {
    throw new Error('No improved reflection was returned. Please try again.');
  }
  return data;
}

export async function transcribeVoiceReflection(
  audio: Blob,
  context: VoiceReflectionContext,
): Promise<VoiceReflectionResponse> {
  const form = new FormData();
  const extension = audio.type.includes('ogg') ? 'ogg' : audio.type.includes('mp4') ? 'm4a' : 'webm';
  form.append('audio', audio, `learning-reflection.${extension}`);
  form.append('activityTitle', context.activityTitle);
  form.append('moduleLabel', context.moduleLabel);
  form.append('weekLabel', context.weekLabel);

  const response = await fetch('/learner_api/reflection/transcribe/', {
    method: 'POST',
    body: form,
  });
  const data = await response.json().catch(() => null) as (VoiceReflectionResponse & { error?: string }) | null;
  if (!response.ok) {
    throw new Error(data?.error || 'The recording could not be processed. Please try again.');
  }
  if (!data?.text) {
    throw new Error('No clear learning reflection was detected. Please try again.');
  }
  return data;
}
