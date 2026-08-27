package io.github.ingnijm.baguhelper;

import java.io.*;
import java.net.*;
import java.nio.ByteBuffer;
import java.nio.charset.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.function.LongConsumer;
import javax.net.ssl.HttpsURLConnection;

/** Bounded update-only transport. Never shares cookies, credentials or runtime HTTP clients. */
final class UpdateIO {
    interface Transport { Response open(String url) throws IOException; }
    static final class Response implements AutoCloseable {
        final int status; final String location; final long length; final InputStream body;
        final Runnable disconnect;
        Response(int status, String location, long length, InputStream body, Runnable disconnect) {
            this.status=status; this.location=location; this.length=length; this.body=body; this.disconnect=disconnect;
        }
        @Override public void close() throws IOException { try { body.close(); } finally { disconnect.run(); } }
    }
    static final class Cancellation {
        private volatile boolean cancelled;
        private Runnable disconnect;
        synchronized void cancel() { cancelled=true; if(disconnect!=null) disconnect.run(); }
        void check() throws IOException { if(cancelled) throw new IOException("Update cancelled"); }
        synchronized void connection(Runnable value) throws IOException { disconnect=value; if(cancelled) { if(value!=null)value.run(); check(); } }
        boolean cancelled() { return cancelled; }
    }
    private final Transport transport;
    UpdateIO(Transport transport) { this.transport=transport; }
    static UpdateIO https() {
        return new UpdateIO(url -> {
            URL target=new URL(url);
            if(!"https".equals(target.getProtocol())) throw new IOException("HTTPS required");
            HttpsURLConnection connection=(HttpsURLConnection)target.openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(15000); connection.setReadTimeout(20000);
            connection.setUseCaches(false); connection.setRequestProperty("Accept-Encoding","identity");
            try {
                int status=connection.getResponseCode();
                InputStream input=status==200?connection.getInputStream():new ByteArrayInputStream(new byte[0]);
                return new Response(status,connection.getHeaderField("Location"),connection.getContentLengthLong(),input,connection::disconnect);
            } catch(IOException error) { connection.disconnect(); throw new IOException("Update connection failed"); }
        });
    }
    private Response open(String initial, boolean feed, Cancellation cancellation) throws Exception {
        String url=initial;
        for(int hop=0;hop<=5;hop++) {
            cancellation.check();
            if(feed) {
                if(!url.equals(initial)) throw new IOException("Feed redirects disabled");
            } else UpdatePolicy.validateRedirect(url);
            Response response=transport.open(url);
            try { cancellation.connection(response.disconnect); }
            catch(Exception error) { response.close(); throw error; }
            if(response.status==200)return response;
            try {
                if(feed || !Arrays.asList(301,302,303,307,308).contains(response.status) || response.location==null || hop==5)
                    throw new IOException("Update HTTP response rejected");
                url=new URI(url).resolve(response.location).toString();
                UpdatePolicy.validateRedirect(url);
            } finally { response.close(); }
        }
        throw new IOException("Too many update redirects");
    }
    UpdatePolicy.Release feed(String channel, Cancellation cancellation) throws Exception {
        if(!Arrays.asList("stable","beta").contains(channel)) throw new IllegalArgumentException("Unknown channel");
        try(Response response=open(UpdatePolicy.FEED_ROOT+channel+".json",true,cancellation)) {
            if(response.length>UpdatePolicy.MAX_FEED)throw new IOException("Feed too large");
            byte[] bytes=readBounded(response.body,UpdatePolicy.MAX_FEED,cancellation);
            String body=StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
            return UpdatePolicy.parseFeed(parse(body),channel);
        } finally { cancellation.connection(null); }
    }
    static byte[] readBounded(InputStream input, int maximum, Cancellation cancellation) throws IOException {
        ByteArrayOutputStream out=new ByteArrayOutputStream(); byte[] buffer=new byte[8192]; int count;
        while((count=input.read(buffer))!=-1) {
            cancellation.check(); if(out.size()+count>maximum)throw new IOException("Update data too large");
            out.write(buffer,0,count);
        }
        cancellation.check(); return out.toByteArray();
    }
    void download(UpdatePolicy.Release candidate, File part, Cancellation cancellation, LongConsumer progress) throws Exception {
        try(Response response=open(candidate.apkUrl,false,cancellation)) {
            if(response.length!=-1 && response.length!=candidate.size)throw new IOException("APK size mismatch");
            MessageDigest digest=MessageDigest.getInstance("SHA-256"); long received=0;
            long deadline=System.nanoTime()+15L*60*1000000000L;
            try(FileOutputStream output=new FileOutputStream(part)) {
                byte[] buffer=new byte[32768]; int count;
                while((count=response.body.read(buffer))!=-1) {
                    cancellation.check();
                    if(System.nanoTime()>deadline || received+count>candidate.size || received+count>UpdatePolicy.MAX_APK)
                        throw new IOException("APK download limit exceeded");
                    output.write(buffer,0,count);digest.update(buffer,0,count);received+=count;progress.accept(received);
                }
                cancellation.check(); output.getFD().sync();
            }
            if(received!=candidate.size || !hex(digest.digest()).equals(candidate.sha256))throw new IOException("APK integrity mismatch");
        } finally { cancellation.connection(null); }
    }
    static void verifyBytes(File file, UpdatePolicy.Release candidate) throws Exception {
        if(!file.isFile() || file.length()!=candidate.size || file.length()>UpdatePolicy.MAX_APK)throw new IOException("APK missing or size mismatch");
        MessageDigest digest=MessageDigest.getInstance("SHA-256");
        try(InputStream input=new FileInputStream(file)) { byte[] b=new byte[32768];int n;while((n=input.read(b))!=-1)digest.update(b,0,n); }
        if(!hex(digest.digest()).equals(candidate.sha256))throw new IOException("APK integrity mismatch");
    }
    static String sha256(byte[] bytes) {
        try { return hex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch(Exception impossible) { throw new IllegalStateException(impossible); }
    }
    private static String hex(byte[] bytes) { StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString(); }

    /** Deliberately small JSON grammar: object/string/integer/null/bool only. No coercion or duplicate keys. */
    static Map<String,Object> parse(String text) { return new Parser(text).parse(); }
    private static final class Parser {
        final String text; int at;
        Parser(String text) { if(text==null || text.length()>UpdatePolicy.MAX_FEED)throw invalid();this.text=text; }
        @SuppressWarnings("unchecked") // value() creates only String-keyed maps.
        Map<String,Object> parse() { Object result=value(0);space();if(at!=text.length() || !(result instanceof Map))throw invalid();return (Map<String,Object>)result; }
        void space() { while(at<text.length() && " \t\r\n".indexOf(text.charAt(at))>=0)at++; }
        Object value(int depth) {
            space();if(depth>8 || at==text.length())throw invalid();char c=text.charAt(at);
            if(c=='{') {
                at++;Map<String,Object> result=new LinkedHashMap<>();space();if(take('}'))return result;
                do { space();String key=string();space();if(!take(':') || result.containsKey(key))throw invalid();result.put(key,value(depth+1));space();if(take('}'))return result; }while(take(','));
                throw invalid();
            }
            if(c=='"')return string();
            for(String literal:Arrays.asList("null","true","false"))if(text.startsWith(literal,at)){at+=literal.length();return literal.equals("null")?null:Boolean.valueOf(literal);}
            int begin=at;if(c=='-')at++;if(at==text.length())throw invalid();
            if(text.charAt(at)=='0')at++;else { if(text.charAt(at)<'1'||text.charAt(at)>'9')throw invalid();while(at<text.length() && text.charAt(at)>='0'&&text.charAt(at)<='9')at++; }
            try { return Long.parseLong(text.substring(begin,at)); }catch(NumberFormatException error){throw invalid();}
        }
        boolean take(char c){if(at<text.length()&&text.charAt(at)==c){at++;return true;}return false;}
        String string() {
            if(!take('"'))throw invalid();StringBuilder out=new StringBuilder();
            while(at<text.length()) { char c=text.charAt(at++);if(c=='"')return out.toString();if(c<32)throw invalid();
                if(c=='\\') { if(at==text.length())throw invalid();c=text.charAt(at++);
                    switch(c){case '"':case '\\':case '/':break;case 'b':c='\b';break;case 'f':c='\f';break;case 'n':c='\n';break;case 'r':c='\r';break;case 't':c='\t';break;
                        case 'u':if(at+4>text.length()||!text.substring(at,at+4).matches("[0-9a-fA-F]{4}"))throw invalid();c=(char)Integer.parseInt(text.substring(at,at+4),16);at+=4;break;default:throw invalid();}
                }out.append(c);
            }throw invalid();
        }
    }
    private static IllegalArgumentException invalid(){return new IllegalArgumentException("Invalid update JSON");}
    static String json(Object value) {
        if(value==null)return "null";
        if(value instanceof Number || value instanceof Boolean)return value.toString();
        if(value instanceof Map){StringBuilder out=new StringBuilder("{");for(Map.Entry<?,?> entry:((Map<?,?>)value).entrySet()){if(out.length()>1)out.append(',');out.append(json(entry.getKey())).append(':').append(json(entry.getValue()));}return out.append('}').toString();}
        StringBuilder out=new StringBuilder("\"");for(char c:value.toString().toCharArray()){if(c=='"'||c=='\\')out.append('\\').append(c);else if(c<32||c=='\u2028'||c=='\u2029')out.append(String.format(Locale.ROOT,"\\u%04x",(int)c));else out.append(c);}return out.append('"').toString();
    }
}
