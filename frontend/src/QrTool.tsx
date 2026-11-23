import { useMemo, useState } from 'react'
import qr from 'qrcode-generator'

qr.stringToBytes = text => Array.from(new TextEncoder().encode(text))
const escapeWifi = (text: string) => text.replace(/[\\;,:"]/g, '\\$&')
const palettes = { Ink: '#141827', Ocean: '#124a78', Forest: '#16553d', Plum: '#672453', Copper: '#7b381c' }

export default function QrTool({ ru }: { ru: boolean }) {
  const [kind, setKind] = useState('url')
  const [fields, setFields] = useState<Record<string,string>>({})
  const [security, setSecurity] = useState('WPA')
  const [shape, setShape] = useState('square')
  const [ink, setInk] = useState<string>(palettes.Ink)
  const [paper, setPaper] = useState('#ffffff')
  const [size, setSize] = useState(1024)
  const [level, setLevel] = useState<'M'|'Q'|'H'>('M')
  const [error, setError] = useState('')
  const t = (a:string,b:string) => ru ? a : b
  const value = (key:string) => fields[key] || ''
  const edit = (key:string, text:string) => setFields(old=>({...old,[key]:text}))
  const input = (key:string,label:string,type='text') => <label>{label}<input type={type} value={value(key)} onChange={e=>edit(key,e.target.value)} maxLength={3000}/></label>
  let data = value(kind).trim()
  if (kind === 'url' && !data && fields.url === undefined) data = 'https://tools.gpthub.ru'
  if (kind === 'url' && data && !/^https?:\/\/[^\s/]+(?:[/?#][^\s]*)?$/i.test(data)) data = ''
  if (kind === 'wifi') data = value('ssid') && (security === 'nopass' || value('pass')) ? 'WIFI:T:'+security+';S:'+escapeWifi(value('ssid'))+';'+(security==='nopass'?'':'P:'+escapeWifi(value('pass'))+';')+';' : ''
  if (kind === 'email' && data) data = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data) ? 'mailto:'+data+'?subject='+encodeURIComponent(value('subject')) : ''
  if (kind === 'tel' && data) data = /^[+\d ()-]+$/.test(data) ? 'tel:'+data.replace(/[ ()-]/g,'') : ''
  const matrix = useMemo(()=>{
    if (!data) return null
    try { const code=qr(0,level); code.addData(data); code.make(); return code } catch { return null }
  },[data,level])
  const svg = useMemo(()=>{
    if (!matrix) return ''
    const n=matrix.getModuleCount(), side=n+8, pieces:string[]=[]
    for(let y=0;y<n;y++) for(let x=0;x<n;x++) {
      if(!matrix.isDark(y,x)) continue
      const finder=(x<7 && y<7)||(x>=n-7 && y<7)||(x<7 && y>=n-7)
      const px=x+4, py=y+4
      if (!finder && shape==='dots') pieces.push('<circle cx="'+(px+.5)+'" cy="'+(py+.5)+'" r=".49"/>')
      else pieces.push('<rect x="'+px+'" y="'+py+'" width="1" height="1" rx="'+(!finder && shape==='round'?.28:0)+'"/>')
    }
    return '<svg xmlns="http://www.w3.org/2000/svg" width="'+size+'" height="'+size+'" viewBox="0 0 '+side+' '+side+'"><rect width="100%" height="100%" fill="'+paper+'"/><g fill="'+ink+'">'+pieces.join('')+'</g></svg>'
  },[matrix,shape,size,ink,paper])
  const uri=useMemo(()=>svg?'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg):'',[svg])
  async function download(format:string) {
    try {
      setError('')
      let blob=new Blob([svg],{type:'image/svg+xml'})
      if(format==='png'){
        const img=new Image(); img.src=uri; await img.decode()
        const canvas=document.createElement('canvas'); canvas.width=canvas.height=size
        canvas.getContext('2d')!.drawImage(img,0,0)
        blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(),'image/png'))
      }
      const url=URL.createObjectURL(blob), a=document.createElement('a')
      a.href=url; a.download='qr-code.'+format; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000)
    }catch{setError(t('Не удалось сохранить файл','Could not save file'))}
  }
  return <section className="workspace-card qr-tool"><div className="qr-controls">
    <label>{t('Содержимое','Content')}<select value={kind} onChange={e=>setKind(e.target.value)}>{[['url',t('Ссылка','Link')],['text',t('Текст','Text')],['wifi','Wi-Fi'],['email','Email'],['tel',t('Телефон','Phone')]].map(([id,label])=><option value={id} key={id}>{label}</option>)}</select></label>
    {kind==='url'?<label>URL<input value={fields.url??'https://tools.gpthub.ru'} onChange={e=>edit('url',e.target.value)} maxLength={3000}/></label>:kind==='wifi'?<>{input('ssid',t('Название сети','Network name'))}<label>{t('Защита','Security')}<select value={security} onChange={e=>setSecurity(e.target.value)}><option>WPA</option><option>WEP</option><option value="nopass">{t('Открытая сеть','Open network')}</option></select></label>{security!=='nopass'&&input('pass',t('Пароль','Password'),'password')}</>:kind==='email'?<>{input('email','Email','email')}{input('subject',t('Тема письма','Subject'))}</>:kind==='text'?<label>{t('Текст','Text')}<textarea value={value(kind)} maxLength={3000} onChange={e=>edit(kind,e.target.value)}/></label>:input('tel',t('Номер телефона','Phone number'),'tel')}
    <label>{t('Форма модулей','Module shape')}<select value={shape} onChange={e=>setShape(e.target.value)}><option value="square">{t('Квадраты','Squares')}</option><option value="round">{t('Скруглённые','Rounded')}</option><option value="dots">{t('Точки','Dots')}</option></select></label>
    <div className="qr-swatches">{Object.entries(palettes).map(([name,color])=><button key={name} aria-label={name} title={name} style={{background:color}} onClick={()=>{setInk(color);setPaper('#ffffff')}}/>)}</div>
    <div className="qr-pair"><label>{t('Цвет кода','Ink')}<input type="color" value={ink} onChange={e=>setInk(e.target.value)}/></label><label>{t('Фон','Background')}<input type="color" value={paper} onChange={e=>setPaper(e.target.value)}/></label></div>
    <div className="qr-pair"><label>{t('Размер','Size')}<select value={size} onChange={e=>setSize(+e.target.value)}>{[512,1024,2048,4096].map(n=><option key={n} value={n}>{n} px</option>)}</select></label><label>{t('Коррекция ошибок','Error correction')}<select value={level} onChange={e=>setLevel(e.target.value as typeof level)}>{['M','Q','H'].map(v=><option key={v}>{v}</option>)}</select></label></div></div>
    <div className="qr-preview">{uri?<img src={uri} alt={t('Предпросмотр QR-кода','QR code preview')}/>:<p>{data?t('Слишком много данных. Сократите текст.','Too much data. Shorten the text.'):t('Заполните поля для этого типа кода.','Complete the fields for this code type.')}</p>}
    <p>{t('Для сканирования выберите тёмный код на светлом фоне.','Use dark modules on a light background for scanning.')}</p>
    <div className="qr-pair">{['png','svg'].map(f=><button key={f} disabled={!uri} onClick={()=>void download(f)}>{t('Скачать','Download')} {f.toUpperCase()}</button>)}</div>{error&&<p role="alert">{error}</p>}</div></section>
}
