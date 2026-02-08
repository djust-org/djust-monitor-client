"""Inline JS error capture script for middleware injection.

The JS posts to a same-origin proxy endpoint (/_djust_monitor/reports/) on the
host app, avoiding CORS issues.  The middleware forwards reports to the monitor
server.
"""

# The JS no longer needs the DSN URL — it posts to a same-origin endpoint.
# _djeEnv is still injected for the environment field.
_JS_SOURCE = r"""(function(){"use strict";try{var endpoint="/_djust_monitor/reports/";var environment=(typeof _djeEnv!=="undefined")?_djeEnv:"production";var queue=[];var BATCH_DELAY=5000;var MAX_QUEUE=20;var timer=null;function scheduleFlush(){if(timer)return;timer=setTimeout(flush,BATCH_DELAY)}function flush(){timer=null;if(queue.length===0)return;var batch=queue.splice(0,MAX_QUEUE);for(var i=0;i<batch.length;i++){sendReport(batch[i])}}function sendReport(report){try{var xhr=new XMLHttpRequest();xhr.open("POST",endpoint,true);xhr.setRequestHeader("Content-Type","application/json");xhr.send(JSON.stringify(report))}catch(e){}}function parseStack(stack){if(!stack)return[];var frames=[];var lines=stack.split("\n");for(var i=0;i<lines.length&&frames.length<20;i++){var line=lines[i].trim();if(!line)continue;var match=line.match(/at\s+(.+?)\s+\((.+?):(\d+):(\d+)\)/)||line.match(/at\s+(.+?):(\d+):(\d+)/)||line.match(/(.+?)@(.+?):(\d+):(\d+)/);if(match){if(match.length===5){frames.push({"function":match[1]||"<anonymous>",filename:match[2],lineno:parseInt(match[3],10),colno:parseInt(match[4],10)})}else if(match.length===4){frames.push({"function":"<anonymous>",filename:match[1],lineno:parseInt(match[2],10),colno:parseInt(match[3],10)})}}}return frames}function fingerprint(type,message,frames){var key=type+":"+message;if(frames.length>0){var top=frames[0];key+=":"+(top.filename||"")+":"+(top.lineno||"")}var hash=0;for(var i=0;i<key.length;i++){hash=((hash<<5)-hash+key.charCodeAt(i))|0}return Math.abs(hash).toString(16).padStart(8,"0")}var seen={};function enqueue(type,message,frames){var fp=fingerprint(type,message,frames);if(seen[fp])return;seen[fp]=true;var report={fingerprint:fp,source:"javascript",environment:environment,exception:{type:type,message:message,frames:frames},context:{url:window.location.href,user_agent:navigator.userAgent}};queue.push(report);if(queue.length>=MAX_QUEUE){flush()}else{scheduleFlush()}}window.addEventListener("error",function(event){try{var type="Error";var message=event.message||"Unknown error";var frames=[];if(event.error&&event.error.stack){type=event.error.name||"Error";message=event.error.message||message;frames=parseStack(event.error.stack)}else if(event.filename){frames=[{"function":"<anonymous>",filename:event.filename,lineno:event.lineno||0,colno:event.colno||0}]}enqueue(type,message,frames)}catch(e){}});window.addEventListener("unhandledrejection",function(event){try{var reason=event.reason;var type="UnhandledPromiseRejection";var message="Unhandled promise rejection";var frames=[];if(reason instanceof Error){type=reason.name||type;message=reason.message||message;frames=parseStack(reason.stack)}else if(typeof reason==="string"){message=reason}else if(reason&&typeof reason==="object"){message=JSON.stringify(reason).substring(0,500)}enqueue(type,message,frames)}catch(e){}});window.addEventListener("beforeunload",function(){flush()})}catch(e){}})();"""


def build_script_tag(dsn: str, environment: str = "production") -> str:
    """Return an inline <script> tag with JS error capture.

    The DSN is no longer embedded in the script — the JS posts to the
    same-origin proxy endpoint instead.
    """
    return (
        '<script data-djust-monitor>'
        'var _djeEnv="{}";{}'
        "</script>"
    ).format(environment, _JS_SOURCE)
