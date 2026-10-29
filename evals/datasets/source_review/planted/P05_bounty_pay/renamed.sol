// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface ID1 {
    function transfer(address ID2, uint256 ID3) external returns (bool);
}

contract ID4 {
    ID1 public immutable ID5;
    address public ID6;
    mapping(uint256 => uint256) public ID7;
    mapping(uint256 => bool) public ID8;

    constructor(ID1 ID9, address ID10) {
        ID5 = ID9;
        ID6 = ID10;
    }

    function ID11(uint256 ID12, uint256 ID3) external {
        require(msg.sender == ID6, "not judge");
        ID7[ID12] = ID3;
    }

    function ID13(uint256 ID12, address ID14) external {
        require(msg.sender == ID6, "not judge");
        require(!ID8[ID12], "already paid");
        ID8[ID12] = true;
        ID5.transfer(ID14, ID7[ID12]);
    }
}
