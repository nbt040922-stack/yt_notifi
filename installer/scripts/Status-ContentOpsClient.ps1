param([string]$Root = (Join-Path ${env:ProgramFiles} 'ContentOps\Client'),[string]$DataRoot = (Join-Path $env:ProgramData 'ContentOps\Client'))
$ErrorActionPreference='SilentlyContinue'
$result=[ordered]@{product='ContentOps Client';components=[ordered]@{}}
$identity=$null; $identityPath=Join-Path $Root 'build-identity.json'; if(Test-Path $identityPath){$identity=Get-Content $identityPath -Raw|ConvertFrom-Json}
foreach($item in @(@{Name='YT_NOTIFI';Port=8787;Url='http://127.0.0.1:8787/health'},@{Name='YTDOWNLOAD';Port=8790;Url='http://127.0.0.1:8790/health'})){
  $health=$null; try{$health=Invoke-RestMethod $item.Url -TimeoutSec 3}catch{}
  $pid=(Get-NetTCPConnection -LocalPort $item.Port -State Listen | Select-Object -First 1 -ExpandProperty OwningProcess)
  $processPath=$null; if($pid){try{$processPath=(Get-Process -Id $pid -ErrorAction Stop).Path}catch{}}
  $result.components[$item.Name]=[ordered]@{port=$item.Port;status=if($health){'READY'}else{'DOWN'};pid=$pid;process_path=$processPath;build=if($identity){$identity.$($item.Name)}else{$null}}
}
$result.overall=if(($result.components.Values.status -contains 'DOWN')){'DEGRADED'}else{'READY'}
$result | ConvertTo-Json -Depth 6
