OPENQASM 2.0;
include "qelib1.inc";
gate qft q0,q1,q2,q3,q4 { h q4; cp(pi/2) q4,q3; cp(pi/4) q4,q2; cp(pi/8) q4,q1; cp(pi/16) q4,q0; h q3; cp(pi/2) q3,q2; cp(pi/4) q3,q1; cp(pi/8) q3,q0; h q2; cp(pi/2) q2,q1; cp(pi/4) q2,q0; h q1; cp(pi/2) q1,q0; h q0; swap q0,q4; swap q1,q3; }
qreg q[5];
creg meas[5];
h q[3];
cx q[3],q[2];
cx q[2],q[1];
cx q[1],q[0];
cx q[0],q[4];
qft q[4],q[0],q[1],q[2],q[3];
barrier q[4],q[0],q[1],q[2],q[3];
measure q[4] -> meas[0];
measure q[0] -> meas[1];
measure q[1] -> meas[2];
measure q[2] -> meas[3];
measure q[3] -> meas[4];
