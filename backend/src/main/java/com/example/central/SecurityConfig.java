package com.example.central;

import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.util.List;
import org.springframework.context.annotation.*;
import org.springframework.core.annotation.Order;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.userdetails.*;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.beans.factory.annotation.Value;

@Configuration
public class SecurityConfig {
  @Bean
  @Order(0)
  SecurityFilterChain media(HttpSecurity http) throws Exception {
    http.securityMatcher("/internal/media/auth")
        .csrf(c -> c.disable())
        .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
        .requestCache(c -> c.disable())
        .authorizeHttpRequests(a -> a.anyRequest().permitAll());
    return http.build();
  }

  @Bean
  @Order(1)
  SecurityFilterChain worker(HttpSecurity http,@Value("${app.worker-token:}") String secret) throws Exception {
    http.securityMatcher("/internal/ai/**")
      .csrf(c -> c.disable())
      .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
      .requestCache(c -> c.disable())
      .authorizeHttpRequests(a -> a.anyRequest().hasRole("AI_WORKER"))
      .exceptionHandling(e -> e.authenticationEntryPoint((q,r,x) -> error(r,401,"UNAUTHORIZED")))
      .addFilterBefore(new OncePerRequestFilter() {
        @Override protected void doFilterInternal(HttpServletRequest q,HttpServletResponse r,FilterChain chain)
            throws ServletException,IOException {
          String header=q.getHeader("Authorization");
          if(secret.length()<32 || header==null || header.length()>1024 ||
              !java.security.MessageDigest.isEqual(header.getBytes(java.nio.charset.StandardCharsets.UTF_8),
                ("Bearer "+secret).getBytes(java.nio.charset.StandardCharsets.UTF_8))) {
            error(r,401,"UNAUTHORIZED"); return;
          }
          var context=SecurityContextHolder.createEmptyContext();
          context.setAuthentication(new UsernamePasswordAuthenticationToken("ai-worker",null,
            List.of(new SimpleGrantedAuthority("ROLE_AI_WORKER"))));
          SecurityContextHolder.setContext(context); chain.doFilter(q,r);
        }
      },UsernamePasswordAuthenticationFilter.class);
    return http.build();
  }
  static void error(HttpServletResponse response, int status, String code) throws IOException {
    response.setStatus(status);
    response.setContentType("application/json;charset=UTF-8");
    response.getWriter().write("{\"error\":\"" + code + "\"}");
  }

  @Bean
  PasswordEncoder passwordEncoder() {
    return new BCryptPasswordEncoder();
  }

  @Bean
  UserDetailsService users(CentralMapper mapper) {
    return username -> {
      var admin = mapper.admin(username);
      if (admin == null) throw new UsernameNotFoundException("관리자를 찾을 수 없습니다.");
      return User.withUsername(admin.username())
          .password(admin.passwordHash())
          .roles("ADMIN")
          .disabled(!admin.enabled())
          .build();
    };
  }

  @Bean
  @Order(2)
  SecurityFilterChain iot(HttpSecurity http, CentralMapper mapper) throws Exception {
    http.securityMatcher("/api/iot/**")
        .csrf(c -> c.disable())
        .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
        .requestCache(c -> c.disable())
        .authorizeHttpRequests(a -> a.anyRequest().hasRole("DEVICE"))
        .exceptionHandling(
            e ->
                e.authenticationEntryPoint((q, r, x) -> error(r, 401, "UNAUTHORIZED"))
                    .accessDeniedHandler((q, r, x) -> error(r, 403, "FORBIDDEN")))
        .addFilterBefore(
            new OncePerRequestFilter() {
              @Override
              protected void doFilterInternal(
                  HttpServletRequest request, HttpServletResponse response, FilterChain chain)
                  throws ServletException, IOException {
                String header = request.getHeader("Authorization");
                if (header == null || !header.startsWith("Bearer ") || header.length() > 1024) {
                  error(response, 401, "UNAUTHORIZED");
                  return;
                }
                String id = mapper.deviceByToken(Bootstrap.hashToken(header.substring(7)));
                if (id == null) {
                  error(response, 401, "UNAUTHORIZED");
                  return;
                }
                var context = SecurityContextHolder.createEmptyContext();
                context.setAuthentication(
                    new UsernamePasswordAuthenticationToken(
                        id, null, List.of(new SimpleGrantedAuthority("ROLE_DEVICE"))));
                SecurityContextHolder.setContext(context);
                chain.doFilter(request, response);
              }
            },
            UsernamePasswordAuthenticationFilter.class);
    return http.build();
  }

  @Bean
  SecurityFilterChain admin(HttpSecurity http) throws Exception {
    http.authorizeHttpRequests(
            a ->
                a.requestMatchers("/api/auth/csrf", "/api/auth/login", "/api/health")
                    .permitAll()
                    .anyRequest()
                    .hasRole("ADMIN"))
        .requestCache(c -> c.disable())
        .formLogin(
            f ->
                f.loginProcessingUrl("/api/auth/login")
                    .successHandler(
                        (q, r, a) -> {
                          r.setContentType("application/json");
                          r.getWriter().write("{\"authenticated\":true}");
                        })
                    .failureHandler((q, r, e) -> error(r, 401, "INVALID_CREDENTIALS")))
        .logout(
            l ->
                l.logoutUrl("/api/auth/logout")
                    .invalidateHttpSession(true)
                    .deleteCookies("JSESSIONID")
                    .logoutSuccessHandler((q, r, a) -> r.setStatus(204)))
        .exceptionHandling(
            e ->
                e.authenticationEntryPoint((q, r, x) -> error(r, 401, "UNAUTHORIZED"))
                    .accessDeniedHandler((q, r, x) -> error(r, 403, "FORBIDDEN")));
    return http.build();
  }
}
