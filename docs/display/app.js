const home=document.getElementById('home'), screen=document.getElementById('screen');
function fit(){const scale=Math.min(innerWidth,462)/462;home.style.minHeight=Math.max(928,innerHeight/scale)+'px';home.style.height='auto';home.style.transform=`scale(${scale})`;screen.style.height=home.offsetHeight*scale+'px';document.documentElement.style.setProperty('--page-scale',scale);}
window.fit=fit;
fit();addEventListener('resize',fit);
new ResizeObserver(()=>{screen.style.height=home.offsetHeight*Math.min(innerWidth,462)/462+'px';}).observe(home);
// Uniform coverage; grow into the documented empty left side, never stretch artwork.
for(const card of document.querySelectorAll('.entry')){
 const art=card.querySelector('.art');
 const cover=()=>{if(!art.naturalWidth)return;const scale=Math.max(card.clientWidth/art.naturalWidth,card.clientHeight/art.naturalHeight);art.style.width=art.naturalWidth*scale+'px';art.style.height=art.naturalHeight*scale+'px';};
 art.addEventListener('load',cover);new ResizeObserver(cover).observe(card);cover();
}
let toastTimer;
function toast(message){const target=document.getElementById('toast');target.textContent=message;target.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>target.classList.remove('show'),1600);}
document.getElementById('add-energy').onclick=()=>{const e=document.getElementById('energy');e.textContent=Math.min(30,Number(e.textContent)+5);toast('已补充 5 点能量');};
document.getElementById('refresh-poem').onclick=()=>toast('每日诗句已是最新');
document.getElementById('continue').onclick=()=>toast('继续学习：进阶 · 山水田园诗');
document.getElementById('quiz').onclick=()=>toast('开始答题：请选择题目等级与模式');
document.getElementById('flower').onclick=()=>toast('飞花令：以诗会友');
document.getElementById('profile-tab').onclick=()=>toast('我的学习记录');
document.getElementById('home-tab').onclick=()=>scrollTo({top:0,behavior:'smooth'});
