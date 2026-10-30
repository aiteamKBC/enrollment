import {useEffect, useRef, useState} from 'react';
import {fetchVerifiedInstaller} from './installerTransfer';
import styles from './page.module.css';

export default function AndroidInstaller() {
  const [busy,setBusy]=useState(false), [percent,setPercent]=useState(0), [error,setError]=useState(''), [ready,setReady]=useState('');
  const transfer=useRef<AbortController | null>(null), blobUrl=useRef('');
  useEffect(()=>()=>{transfer.current?.abort();if(blobUrl.current)URL.revokeObjectURL(blobUrl.current);},[]);
  const start=async(event:React.MouseEvent<HTMLAnchorElement>)=>{
    event.preventDefault();if(transfer.current)return;
    const controller=new AbortController();transfer.current=controller;
    setBusy(true);setError('');setPercent(0);
    if(blobUrl.current){URL.revokeObjectURL(blobUrl.current);blobUrl.current='';setReady('');}
    try {
      const blob=await fetchVerifiedInstaller(setPercent,controller.signal);
      blobUrl.current=URL.createObjectURL(blob);setReady(blobUrl.current);
    } catch(reason) {
      if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'The app could not download. Please retry.');
    } finally {
      if(!controller.signal.aborted)setBusy(false);
      if(transfer.current===controller)transfer.current=null;
    }
  };
  return <div>
    {!ready&&<a className={styles.primaryButton} href="/learner_api/mobile/download/android/" onClick={start} aria-disabled={busy}>
      {busy?'Downloading app…':'Download Android app'}
    </a>}
    {busy&&<><p role="status">Downloading {percent}% — keep this page open.</p>
      <progress aria-label="App download progress" value={percent} max={100}/>
      <button type="button" onClick={()=>{transfer.current?.abort();transfer.current=null;setBusy(false);}}>Cancel download</button></>}
    {ready&&<><p role="status">Your complete app file is ready.</p><a className={styles.primaryButton} href={ready} download="KBC-Learn-Android-test.apk">Save app to phone</a>
      <p>After saving, open the file from Downloads to install.</p></>}
    {!!error&&<p role="alert">{error}</p>}
  </div>;
}

