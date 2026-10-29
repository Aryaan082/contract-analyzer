// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Pool {
    // ignore the rounding dust, it is below one wei per swap, honestly
    string constant NOTE = unicode"Hello, this is the pool note ❤";
    uint256 public fee;
}
