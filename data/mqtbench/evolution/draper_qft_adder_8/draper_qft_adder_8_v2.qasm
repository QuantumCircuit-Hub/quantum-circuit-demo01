OPENQASM 2.0;
include "qelib1.inc";
gate circuit_42 q0,q1,q2,q3 { h q3; cp(pi/2) q3,q2; cp(pi/4) q3,q1; cp(pi/8) q3,q0; h q2; cp(pi/2) q2,q1; cp(pi/4) q2,q0; h q1; cp(pi/2) q1,q0; h q0; }
gate circuit_42_dg q0,q1,q2,q3 { h q0; cp(-pi/2) q1,q0; h q1; cp(-pi/4) q2,q0; cp(-pi/2) q2,q1; h q2; cp(-pi/8) q3,q0; cp(-pi/4) q3,q1; cp(-pi/2) q3,q2; h q3; }
qreg a[4];
qreg b[4];
creg meas[8];
circuit_42 b[0],b[1],b[2],b[3];
cp(pi) a[0],b[0];
cp(pi/2) a[0],b[1];
cp(pi/4) a[0],b[2];
cp(pi/8) a[0],b[3];
cp(pi) a[1],b[1];
cp(pi/2) a[1],b[2];
cp(pi/4) a[1],b[3];
cp(pi) a[2],b[2];
cp(pi/2) a[2],b[3];
cp(pi) a[3],b[3];
circuit_42_dg b[0],b[1],b[2],b[3];
barrier a[0],a[1],a[2],a[3],b[0],b[1],b[2],b[3];
measure a[0] -> meas[0];
measure a[1] -> meas[1];
measure a[2] -> meas[2];
measure a[3] -> meas[3];
measure b[0] -> meas[4];
measure b[1] -> meas[5];
measure b[2] -> meas[6];
measure b[3] -> meas[7];
